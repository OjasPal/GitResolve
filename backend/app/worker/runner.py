"""Worker entry point: `python -m app.worker.runner`.

Claims dispatched tasks from the store, runs the agent outside any API request,
and writes results back through `WorkerService`. Repository code never runs
here; the isolated sandbox is invoked by the agent.
"""

import logging
import socket
import threading
import time
import uuid

from app.agent.interface import AgentRunner, AgentTask
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.errors import public_error, PersistenceError
from app.core.redaction import redact
from app.services.outcomes import OUTCOME_CODES
from app.schemas.enums import DispatchPhase, FailureCode, TaskStage, EventType, ArtifactType
from app.services.github import WorkerGitHubGateway
from app.services.store import build_store
from app.services.ids import sha256_hash
from app.services.transitions import LeaseLost, artifact_path
from app.services.worker_service import ClaimedJob, WorkerService, ExecutionLimit
from app.worker.context import CancelRequested, JobContext
from app.worker.pr import PullRequestCreator, PullRequestError

log = logging.getLogger("gitresolve.worker")


class _HeartbeatThread(threading.Thread):
    """Keeps the lease alive during long LLM/sandbox calls."""

    def __init__(self, service: WorkerService, task_id: str, worker_id: str, interval: float):
        super().__init__(daemon=True)
        self._svc, self._task_id, self._worker_id = service, task_id, worker_id
        self._interval = interval
        self._halt = threading.Event()
        self.stopped = threading.Event()
        self.failure_count = 0

    def run(self) -> None:
        while not self._halt.wait(self._interval):
            try:
                if self._svc.heartbeat(self._task_id, self._worker_id):
                    self.stopped.set()
                    self._svc.mark_cancelled(self._task_id, self._worker_id)
                    return
            except ExecutionLimit as exc:
                self.stopped.set()
                try:
                    task = self._svc.store.get(f"tasks/{self._task_id}")
                    self._svc.fail(self._task_id, self._worker_id, exc.code,
                                   "Task execution limit reached.", DispatchPhase(task["dispatchPhase"]))
                except LeaseLost:
                    pass
                return
            except LeaseLost:
                self.stopped.set()
                return
            except Exception:  # transient store errors: retry next tick
                self.failure_count += 1
                try:
                    fields = self._svc.lease_diagnostics(self._task_id, self.failure_count)
                    task = self._svc.store.get(f"tasks/{self._task_id}") or {}
                    if not task.get("leaseExpiresAt") or task["leaseExpiresAt"] <= self._svc.clock():
                        self.stopped.set()
                except Exception:
                    fields = {"task_id": self._task_id, "failure_count": self.failure_count}
                    self.stopped.set()  # cannot establish lease validity
                log.exception("heartbeat failed", extra=fields)
                if self.stopped.is_set():
                    return
            else:
                self.failure_count = 0
                log.debug("heartbeat renewed", extra=self._svc.lease_diagnostics(self._task_id))

    def stop(self) -> None:
        self._halt.set()


class Worker:
    def __init__(
        self,
        service: WorkerService,
        agent: AgentRunner,
        pr_creator: PullRequestCreator,
        settings: Settings,
        worker_id: str | None = None,
        github: WorkerGitHubGateway | None = None,
    ) -> None:
        self.github = github
        self.service = service
        self.agent = agent
        self.pr_creator = pr_creator
        self.settings = settings
        self.worker_id = worker_id or f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"

    def run_once(self) -> bool:
        """Claim and process at most one job. Returns True if a job was processed."""
        job = self.service.claim_next(self.worker_id)
        if job is None:
            return False
        self.process(job)
        return True

    def run_forever(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        last_reap = 0.0
        while not stop.is_set():
            try:
                if time.monotonic() - last_reap >= self.settings.reaper_interval_seconds:
                    self.service.reap_expired()
                    last_reap = time.monotonic()
                if not self.run_once():
                    stop.wait(self.settings.poll_interval_seconds)
            except Exception:
                # Store outages must not silently kill an embedded worker. A job
                # whose final write failed retains its lease for safe reaping.
                log.exception("worker iteration failed; retrying after poll interval")
                stop.wait(self.settings.poll_interval_seconds)

    def process(self, job: ClaimedJob) -> None:
        beat = _HeartbeatThread(self.service, job.task_id, self.worker_id, self.settings.heartbeat_seconds)
        log.info("processing job", extra=self.service.lease_diagnostics(job.task_id))
        beat.start()
        ctx = JobContext(self.service, job.task_id, self.worker_id, self.github)
        ctx.stopped = beat.stopped
        from contextlib import ExitStack
        from githubapp.timing import model_dispatch_scope, observe_timings
        timing_scope = ExitStack()
        timing_scope.enter_context(model_dispatch_scope(ctx.model_dispatched))
        timing_scope.enter_context(observe_timings(ctx.observe_timing))
        try:
            ctx.checkpoint()
            if job.phase == DispatchPhase.PLAN:
                self._plan(job, ctx)
            elif job.phase == DispatchPhase.EXECUTE:
                self._execute(job, ctx)
            else:
                self._create_pr(job)
        except ExecutionLimit as exc:
            self._safely(lambda: self.service.fail(job.task_id, self.worker_id, exc.code,
                                                 "Task execution limit reached.", job.phase))
        except CancelRequested:
            self._safely(lambda: self.service.mark_cancelled(job.task_id, self.worker_id))
        except LeaseLost:
            log.warning("lease lost; abandoning task", extra={"task_id": job.task_id})
        except PullRequestError as exc:
            self._safely(lambda: self.service.fail(job.task_id, self.worker_id, exc.code, exc.message, job.phase))
        except Exception as exc:
            from agent.router import AllProvidersFailedError, ProviderError
            from app.services.github import GitHubUnavailable
            from githubapp.client import GitHubClientError
            if isinstance(exc, (ProviderError, AllProvidersFailedError)):
                if getattr(exc, "reason", None) == "quality":
                    self._safely(
                        lambda: self.service.fail(
                            job.task_id,
                            self.worker_id,
                            FailureCode.NO_FIX_FOUND,
                            "The revised approach still could not be verified. Failure history is saved; refine the request before another run.",
                            job.phase,
                        )
                    )
                else:
                    self._safely(
                        lambda: self.service.fail(
                            job.task_id,
                            self.worker_id,
                            FailureCode.LLM_UNAVAILABLE,
                            "Investigation temporarily paused. Your progress is saved. Try again once the model service is available.",
                            job.phase,
                            outcome="infra_retry",
                        )
                    )
                return
            if isinstance(exc, (GitHubUnavailable, GitHubClientError)):
                code = getattr(exc, "code", str(exc))
                failure = FailureCode(code) if code in {c.value for c in FailureCode} else FailureCode.GITHUB_ERROR
                self._safely(lambda: self.service.fail(job.task_id, self.worker_id, failure,
                                                      "GitHub operation failed.", job.phase))
                return
            log.exception("job failed", extra={"task_id": job.task_id})
            if job.phase == DispatchPhase.EXECUTE:
                existing_doc = self.service.store.get(f"tasks/{job.task_id}") or {}
                existing_summary = existing_doc.get("finalDiffSummary") or {}
                if existing_summary.get("setupNote") and existing_summary.get("envVars"):
                    from app.agent.lanes.external_guidance import build_curated_guidance_outcome

                    g_outcome = build_curated_guidance_outcome(
                        reason_code="external_secret_missing",
                        provider_key=str(existing_summary.get("providerKey") or "generic"),
                        env_vars=tuple(str(x) for x in (existing_summary.get("envVars") or []) if x),
                        selected_items=(),
                    )
                    if existing_summary.get("whatWeFound"):
                        g_outcome["whatWeFound"] = list(existing_summary["whatWeFound"])
                        g_outcome["evidence"] = list(existing_summary["whatWeFound"])
                    self._safely(
                        lambda: self.service.complete_guidance(
                            job.task_id,
                            self.worker_id,
                            g_outcome,
                            summary=existing_summary,
                            verification=existing_doc.get("verification") or {},
                        )
                    )
                    return
            message = public_error(exc).message
            self._safely(
                lambda: self.service.fail(
                    job.task_id, self.worker_id, FailureCode.INTERNAL_ERROR, message, job.phase,
                    outcome="infra_retry" if isinstance(exc, (PersistenceError, TimeoutError, ConnectionError)) else None
                )
            )
        finally:
            timing_scope.__exit__(None, None, None)
            ctx.flush_timing()
            beat.stop()
            beat.join(timeout=2)

    def _safely(self, fn) -> None:
        try:
            fn()
        except LeaseLost:
            log.warning("lease lost while finalising")

    @staticmethod
    def _agent_task(job: ClaimedJob) -> AgentTask:
        t = job.task
        return AgentTask(
            task_id=t["id"], repo_id=t["repoId"], repo_full_name=t["repoFullName"],
            default_branch=t["defaultBranch"], base_sha=t.get("baseSha"),
            issue={**t["issue"], "body": (t["issue"].get("body") or "") + ("\n\nHuman review refinements (untrusted request context):\n" + t["revisionContext"] if t.get("revisionContext") else "") + ("\n\nPrevious proposed patch (untrusted context; regenerate and verify against the pinned base):\n" + t["revisionPatch"] if t.get("revisionPatch") else "")},
            plan=t.get("plan"), limits=t["limits"], attempts_used=t["attempts"]["used"], cleanup_scope=t.get("cleanupScope"),
        )

    def _plan(self, job: ClaimedJob, ctx: JobContext) -> None:
        if self.github is not None:
            job.task["baseSha"] = self.github.resolve(job.task)
            ctx.stage(TaskStage.PLAN, baseSha=job.task["baseSha"])
            ctx.event(EventType.TOOL_CALL, "GitHub repository access verified and base commit pinned.")
        result = self.agent.plan(self._agent_task(job), ctx)
        ctx.checkpoint()
        if result.outcome == "ready" and result.plan is not None:
            self.service.complete_plan(job.task_id, self.worker_id, result.plan)
            from app.worker.prewarm import start
            start(self.service, self.github, job.task_id)
        elif result.outcome == "needs_input":
            self.service.fail(
                job.task_id, self.worker_id, FailureCode.ISSUE_AMBIGUOUS,
                "The issue needs more detail.", DispatchPhase.PLAN, retryable=True, plan=result.plan,
            )
        else:
            self.service.fail(
                job.task_id, self.worker_id,
                result.failure_code or OUTCOME_CODES.get(result.outcome, FailureCode.INTERNAL_ERROR),
                result.failure_message or "Planning failed.", DispatchPhase.PLAN,
                outcome=result.outcome if result.outcome in OUTCOME_CODES else None,
                evidence=result.evidence, next_step=result.next_step, plan=result.plan,
            )

    def _execute(self, job: ClaimedJob, ctx: JobContext) -> None:
        result = self.agent.execute(self._agent_task(job), ctx)
        ctx.checkpoint()
        if result.outcome == "fix_ready" and result.final_diff:
            self.service.complete_execution(
                job.task_id, self.worker_id, result.final_diff, result.final_tests, result.summary, result.verification
            )
        elif result.outcome == "resolved_with_guidance":
            guidance_outcome = (result.summary or {}).get("guidanceOutcome") or {
                "kind": "needs_user_action",
                "reasonCode": "external_secret_missing",
                "title": result.failure_message or "External setup required",
                "summary": result.failure_message or "Complete the external setup steps to resolve this issue.",
                "whatWeFound": list(result.evidence or []),
                "steps": [{"text": result.next_step or "Configure the required external setting in your environment."}],
                "afterYouDoThis": result.next_step or "Re-check once the setting is active.",
                "securityNote": "Never paste API keys or secrets into issues or chat.",
            }
            self.service.complete_guidance(
                job.task_id,
                self.worker_id,
                guidance_outcome,
                summary=result.summary,
                verification=result.verification,
                final_tests=result.final_tests,
            )
        else:
            self.service.fail(
                job.task_id, self.worker_id,
                result.failure_code or OUTCOME_CODES.get(result.outcome, FailureCode.NO_FIX_FOUND),
                result.failure_message or "No fix was produced.", DispatchPhase.EXECUTE,
                outcome=result.outcome if result.outcome in OUTCOME_CODES else None,
                evidence=result.evidence, next_step=result.next_step,
            )

    def _create_pr(self, job: ClaimedJob) -> None:
        task = job.task
        artifact = self.service.store.get(artifact_path(job.task_id, "final_diff"))
        if artifact is None:
            raise PullRequestError(FailureCode.INTERNAL_ERROR, "Approved diff artifact is missing.")
        approval = task.get("approval") or {}
        actual_hash = sha256_hash(artifact["content"])
        if (
            approval.get("state") != "approved"
            or approval.get("diffHash") != actual_hash
            or task.get("finalDiffHash") != actual_hash
            or artifact.get("truncated")
            or redact(artifact["content"]) != artifact["content"]
        ):
            raise PullRequestError(FailureCode.INTERNAL_ERROR, "Stored diff does not match the approved diff.")
        summary_doc = task.get("finalDiffSummary") or {}
        setup_note = str(summary_doc.get("setupNote") or "").strip()
        if setup_note:
            expected_setup_hash = approval.get("setupNoteHash") or summary_doc.get("setupNoteHash")
            if (
                not expected_setup_hash
                or sha256_hash(setup_note) != expected_setup_hash
                or redact(setup_note) != setup_note
                or len(setup_note) > 600
            ):
                raise PullRequestError(FailureCode.INTERNAL_ERROR, "Stored setup note does not match the approved setup note.")
        self.service.heartbeat(job.task_id, self.worker_id)
        if self.github is not None:
            from agent.publication import build_publication_graph
            ctx = JobContext(self.service, job.task_id, self.worker_id, self.github)
            def publish():
                return self.pr_creator.create(task, artifact["content"], checkpoint=ctx.checkpoint,
                    event=lambda message: ctx.event(EventType.TOOL_CALL, message))
            published = build_publication_graph(publish).invoke({"task_id": job.task_id})
            from app.worker.pr import PullRequestResult
            pr = PullRequestResult(published["pr_number"], published["pr_url"], published["pr_branch"])
            import json
            ctx.artifact("github_pr", ArtifactType.PR_RESULT, json.dumps({
                "number": pr.number, "url": pr.url, "branch": pr.branch}))
        else:
            pr = self.pr_creator.create(task, artifact["content"])
        self.service.complete_pr(job.task_id, self.worker_id, pr.number, pr.url, pr.branch)


def build_worker(
    settings: Settings | None = None,
    store=None,
    pr_creator: PullRequestCreator | None = None,
) -> Worker:
    settings = settings or get_settings()
    store = store or build_store(settings)
    if settings.agent_mode == "langgraph":
        from app.agent.langgraph_adapter import LangGraphAgentRunner

        agent: AgentRunner = LangGraphAgentRunner()
    else:
        from app.agent.fake import FakeAgentRunner

        agent = FakeAgentRunner()
    github = None
    if settings.github_mode == "real":
        if settings.agent_mode != "langgraph":
            raise RuntimeError("Real GitHub mode requires AGENT_MODE=langgraph; fixture repair cannot publish.")
        from app.services.github_real import RealGitHubGateway
        from app.worker.github_pr import GitHubPullRequestCreator
        github = RealGitHubGateway(settings, store)
        pr_creator = pr_creator or GitHubPullRequestCreator(github)
    if pr_creator is None:
        if settings.environment == "production":
            raise RuntimeError("Production worker requires a real PR creator.")
        from app.worker.pr import FakePullRequestCreator

        pr_creator = FakePullRequestCreator()
    return Worker(WorkerService(store, settings), agent, pr_creator, settings, github=github)


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    settings.assert_safe_for_worker()
    worker = build_worker(settings)
    log.info("worker started id=%s agent_mode=%s", worker.worker_id, settings.agent_mode)
    worker.run_forever()


if __name__ == "__main__":
    main()
