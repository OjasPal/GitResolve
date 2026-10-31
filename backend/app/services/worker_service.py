"""Worker-side operations. Every call except `claim_next` and `reap_expired`
must come from the current lease owner; otherwise `LeaseLost` is raised."""

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.core.config import Settings
from app.core.errors import invalid_state
from app.core.redaction import redact
from app.schemas.enums import (
    ApprovalState,
    ArtifactType,
    DispatchPhase,
    EventLevel,
    EventType,
    FailureCode,
    TaskStage,
    TaskStatus,
)
from app.schemas.tasks import PlanData
from app.services.ids import sha256_hash
from app.services.lifecycle import is_active, status_for_failure
from app.services.store.base import Doc, Store, Txn
from app.services.task_service import utcnow
from app.services.transitions import (
    Ctx,
    EventSpec,
    LeaseLost,
    apply_change,
    artifact_path,
    read_ctx,
    release_lease,
)

ARTIFACT_MAX_BYTES = 256 * 1024
MAX_ARTIFACTS_PER_TASK = 20
PLAN_MAX_BYTES = 8 * 1024


class ExecutionLimit(Exception):
    def __init__(self, code: FailureCode):
        self.code = code
        super().__init__(code.value)

# Structured fields a worker may set through `update_progress`.
PROGRESS_FIELDS = frozenset({"baseSha", "testCommand", "baseline", "reproduction"})
_PHASE_FOR_STATUS = {
    TaskStatus.PLANNING: DispatchPhase.PLAN,
    TaskStatus.RUNNING: DispatchPhase.EXECUTE,
    TaskStatus.CREATING_PR: DispatchPhase.PR,
}


@dataclass(frozen=True)
class ClaimedJob:
    task_id: str
    phase: DispatchPhase
    task: Doc


class WorkerService:
    def __init__(self, store: Store, settings: Settings, clock: Callable[[], datetime] = utcnow):
        self.store = store
        self.settings = settings
        self.clock = clock

    # --- Internals ----------------------------------------------------------------
    def _owned(self, txn: Txn, task_id: str, worker_id: str) -> Ctx:
        try:
            ctx = read_ctx(txn, task_id)
        except Exception:
            raise LeaseLost(task_id) from None
        if ctx.task.get("leaseOwner") != worker_id or not is_active(ctx.task["status"]):
            raise LeaseLost(task_id)
        expires = ctx.task.get("leaseExpiresAt")
        if expires is None or expires <= self.clock():
            raise LeaseLost(task_id)
        return ctx

    def _lease_updates(self, now: datetime, worker_id: str) -> dict[str, Any]:
        return {
            "leaseOwner": worker_id,
            "leaseExpiresAt": now + timedelta(seconds=self.settings.lease_seconds),
            "heartbeatAt": now,
        }

    # --- Claim / heartbeat ------------------------------------------------------------
    def claim_next(self, worker_id: str) -> ClaimedJob | None:
        for candidate in self.store.list_pending_dispatch(10):
            job = self.store.run_transaction(lambda txn, tid=candidate["id"]: self._claim(txn, tid, worker_id))
            if job is not None:
                return job
        return None

    def _claim(self, txn: Txn, task_id: str, worker_id: str) -> ClaimedJob | None:
        now = self.clock()
        ctx = read_ctx(txn, task_id)
        task = ctx.task
        if not task.get("dispatchPending") or task.get("leaseOwner"):
            return None
        phase = DispatchPhase(task["dispatchPhase"])
        status = TaskStatus(task["status"])
        expected = {
            DispatchPhase.PLAN: TaskStatus.QUEUED,
            DispatchPhase.EXECUTE: TaskStatus.RUNNING,
            DispatchPhase.PR: TaskStatus.CREATING_PR,
        }[phase]
        if status != expected:
            # Inconsistent dispatch marker: drop it rather than run the wrong phase.
            apply_change(txn, ctx, self.settings, now, updates={"dispatchPending": False})
            return None
        updates = {"dispatchPending": False, **self._lease_updates(now, worker_id)}
        if phase == DispatchPhase.PLAN:
            updates["startedAt"] = now
            new = apply_change(
                txn, ctx, self.settings, now,
                to_status=TaskStatus.PLANNING, stage=TaskStage.INTAKE, updates=updates,
            )
        else:
            new = apply_change(
                txn, ctx, self.settings, now,
                updates=updates,
                events=[EventSpec(EventType.INFO, f"Worker claimed {phase.value} phase.")],
            )
        return ClaimedJob(task_id=task_id, phase=phase, task=new)

    def heartbeat(self, task_id: str, worker_id: str) -> bool:
        """Renew the lease. Returns True if cancellation was requested."""
        now = self.clock()

        def fn(txn: Txn) -> bool:
            ctx = self._owned(txn, task_id, worker_id)
            if ctx.task.get("deadlineAt") and now >= ctx.task["deadlineAt"]:
                raise ExecutionLimit(FailureCode.TIMEOUT)
            txn.update(f"tasks/{task_id}", self._lease_updates(now, worker_id))
            return bool(ctx.task.get("cancelRequested"))

        return self.store.run_transaction(fn)

    # --- Progress -----------------------------------------------------------------------
    def update_progress(
        self,
        task_id: str,
        worker_id: str,
        *,
        stage: TaskStage | None = None,
        fields: dict[str, Any] | None = None,
        attempts_used: int | None = None,
        budgets: dict[str, Any] | None = None,
        events: list[EventSpec] | None = None,
    ) -> Doc:
        unknown = set(fields or {}) - PROGRESS_FIELDS
        if unknown:
            raise ValueError(f"Fields not writable by worker: {sorted(unknown)}")
        now = self.clock()

        def fn(txn: Txn) -> Doc:
            ctx = self._owned(txn, task_id, worker_id)
            updates: dict[str, Any] = dict(fields or {})
            limits = ctx.task["limits"]
            if attempts_used is not None:
                if type(attempts_used) is not int or attempts_used < ctx.task["attempts"]["used"]:
                    raise ValueError("Attempt counters must be monotonic integers.")
                if attempts_used > limits["maxRepairAttempts"]:
                    raise ExecutionLimit(FailureCode.BUDGET_EXCEEDED)
            for key, value in (budgets or {}).items():
                if key not in ctx.task["budgets"] or type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError("Invalid usage counter.")
                if value < ctx.task["budgets"][key]:
                    raise ValueError("Usage counters must be monotonic.")
                cap = {"modelCalls": limits["maxModelCalls"], "execSeconds": limits["taskTimeoutSeconds"]}.get(key)
                if cap is not None and value > cap:
                    raise ExecutionLimit(FailureCode.BUDGET_EXCEEDED)
            if attempts_used is not None:
                updates["attempts"] = {**ctx.task["attempts"], "used": attempts_used}
            if budgets is not None:
                updates["budgets"] = {**ctx.task["budgets"], **budgets}
            return apply_change(txn, ctx, self.settings, now, stage=stage, updates=updates, events=events)

        return self.store.run_transaction(fn)

    def append_event(self, task_id: str, worker_id: str, spec: EventSpec) -> None:
        now = self.clock()

        def fn(txn: Txn) -> None:
            ctx = self._owned(txn, task_id, worker_id)
            apply_change(txn, ctx, self.settings, now, events=[spec])

        self.store.run_transaction(fn)

    def _artifact_doc(
        self, task: Doc, artifact_id: str, type_: ArtifactType, content: str,
        attempt: int | None, now: datetime,
    ) -> Doc:
        clean = redact(content)
        if type_ == ArtifactType.FINAL_DIFF and clean != content:
            raise ValueError("final_diff cannot be stored without changing its validated content.")
        raw = clean.encode()
        truncated = len(raw) > ARTIFACT_MAX_BYTES
        if truncated:
            clean = raw[:ARTIFACT_MAX_BYTES].decode(errors="ignore")
        return {
            "id": artifact_id,
            "type": type_.value,
            "attempt": attempt,
            "contentType": "application/json" if type_ == ArtifactType.PLAN else "text/plain",
            "content": clean,
            "sizeBytes": len(clean.encode()),
            "truncated": truncated,
            "sha256": sha256_hash(clean).removeprefix("sha256:"),
            "createdAt": now,
            "expiresAt": task["createdAt"] + timedelta(days=self.settings.subdoc_retention_days),
        }

    def save_artifact(
        self, task_id: str, worker_id: str, artifact_id: str, type_: ArtifactType,
        content: str, attempt: int | None = None,
    ) -> Doc:
        now = self.clock()
        existing_ids = {a["id"] for a in self.store.list_artifacts(task_id)}
        if artifact_id not in existing_ids and len(existing_ids) >= MAX_ARTIFACTS_PER_TASK:
            raise invalid_state("Artifact limit reached for this task.")

        def fn(txn: Txn) -> Doc:
            ctx = self._owned(txn, task_id, worker_id)
            doc = self._artifact_doc(ctx.task, artifact_id, type_, content, attempt, now)
            txn.set(artifact_path(task_id, artifact_id), doc)
            return doc

        return self.store.run_transaction(fn)

    # --- Completion ----------------------------------------------------------------------
    def complete_plan(self, task_id: str, worker_id: str, plan: PlanData) -> Doc:
        plan_doc = plan.model_dump(by_alias=True)
        plan_json = json.dumps(plan_doc)
        if len(plan_json.encode()) > PLAN_MAX_BYTES:
            raise ValueError("Plan exceeds 8 KB.")
        now = self.clock()

        def fn(txn: Txn) -> Doc:
            ctx = self._owned(txn, task_id, worker_id)
            txn.set(
                artifact_path(task_id, "plan"),
                self._artifact_doc(ctx.task, "plan", ArtifactType.PLAN, plan_json, None, now),
            )
            return apply_change(
                txn, ctx, self.settings, now,
                to_status=TaskStatus.AWAITING_START,
                stage=TaskStage.PLAN,
                updates={"plan": plan_doc, "dispatchPhase": None, **release_lease()},
                events=[EventSpec(EventType.INFO, "Plan ready for review.")],
            )

        return self.store.run_transaction(fn)

    def complete_execution(
        self, task_id: str, worker_id: str, final_diff: str, final_tests: str,
        summary: dict[str, Any],
    ) -> Doc:
        """Store the validated diff and move to `awaiting_approval`.

        The hash is computed here from the stored artifact content, so the
        approval hash always matches what the user reviewed.
        """
        now = self.clock()

        def fn(txn: Txn) -> Doc:
            ctx = self._owned(txn, task_id, worker_id)
            diff_doc = self._artifact_doc(ctx.task, "final_diff", ArtifactType.FINAL_DIFF, final_diff, None, now)
            tests_doc = self._artifact_doc(ctx.task, "final_tests", ArtifactType.FINAL_TESTS, final_tests, None, now)
            if diff_doc["truncated"]:
                raise ValueError("final_diff exceeds the artifact size limit.")
            txn.set(artifact_path(task_id, "final_diff"), diff_doc)
            txn.set(artifact_path(task_id, "final_tests"), tests_doc)
            return apply_change(
                txn, ctx, self.settings, now,
                to_status=TaskStatus.AWAITING_APPROVAL,
                stage=TaskStage.AWAITING_APPROVAL,
                updates={
                    "finalDiffHash": sha256_hash(diff_doc["content"]),
                    "finalDiffSummary": summary,
                    "approval": {"state": ApprovalState.PENDING.value, "by": None, "at": None, "diffHash": None},
                    "dispatchPhase": None,
                    **release_lease(),
                },
                events=[EventSpec(EventType.INFO, "Validated fix ready for review.")],
            )

        return self.store.run_transaction(fn)

    def complete_pr(self, task_id: str, worker_id: str, number: int, url: str, branch: str) -> Doc:
        """Call only after GitHub confirmed PR creation."""
        now = self.clock()

        def fn(txn: Txn) -> Doc:
            ctx = self._owned(txn, task_id, worker_id)
            return apply_change(
                txn, ctx, self.settings, now,
                to_status=TaskStatus.COMPLETED,
                stage=TaskStage.COMPLETED,
                updates={
                    "pr": {"number": number, "url": url, "branch": branch, "createdAt": now},
                    "result": {"outcome": "pr_created"},
                    "dispatchPhase": None,
                    **release_lease(),
                },
                events=[EventSpec(EventType.PR_CREATED, "Pull request created.", data={"number": number})],
            )

        return self.store.run_transaction(fn)

    def fail(
        self, task_id: str, worker_id: str, code: FailureCode, message: str,
        phase: DispatchPhase, retryable: bool = True, plan: PlanData | None = None,
    ) -> Doc:
        """Stop the task. The backend, not the agent, maps `code` to a status."""
        now = self.clock()
        target = status_for_failure(code)

        def fn(txn: Txn) -> Doc:
            ctx = self._owned(txn, task_id, worker_id)
            updates: dict[str, Any] = {
                "failure": {
                    "code": code.value,
                    "message": redact(message, 500),
                    "phase": phase.value,
                    "retryable": retryable,
                },
                "dispatchPhase": None,
                **release_lease(),
            }
            if plan is not None:
                updates["plan"] = plan.model_dump(by_alias=True)
            return apply_change(
                txn, ctx, self.settings, now,
                to_status=target, updates=updates,
                events=[EventSpec(EventType.ERROR, message, level=EventLevel.ERROR, data={"code": code.value})],
            )

        return self.store.run_transaction(fn)

    def mark_cancelled(self, task_id: str, worker_id: str) -> Doc:
        now = self.clock()

        def fn(txn: Txn) -> Doc:
            ctx = self._owned(txn, task_id, worker_id)
            return apply_change(
                txn, ctx, self.settings, now,
                to_status=TaskStatus.CANCELLED,
                updates={"dispatchPhase": None, **release_lease()},
                events=[EventSpec(EventType.CANCELLATION, "Task cancelled.")],
            )

        return self.store.run_transaction(fn)

    # --- Reaper ---------------------------------------------------------------------------
    def reap_expired(self) -> list[str]:
        """Mark tasks whose lease expired as `worker_lost` and free the user's slot."""
        now = self.clock()
        reaped: list[str] = []
        for candidate in self.store.list_expired_leases(now, 50):
            done = self.store.run_transaction(lambda txn, tid=candidate["id"]: self._reap(txn, tid, now))
            if done:
                reaped.append(candidate["id"])
        return reaped

    def _reap(self, txn: Txn, task_id: str, now: datetime) -> bool:
        ctx = read_ctx(txn, task_id)
        task = ctx.task
        expires = task.get("leaseExpiresAt")
        if expires is None or expires > now:
            return False
        status = TaskStatus(task["status"])
        if status not in _PHASE_FOR_STATUS:
            apply_change(txn, ctx, self.settings, now, updates=release_lease())
            return False
        phase = _PHASE_FOR_STATUS[status]
        apply_change(
            txn, ctx, self.settings, now,
            to_status=TaskStatus.WORKER_LOST,
            updates={
                "failure": {
                    "code": FailureCode.WORKER_LOST.value,
                    "message": "The worker stopped responding. Run the task again.",
                    "phase": phase.value,
                    "retryable": True,
                },
                "dispatchPending": False,
                "dispatchPhase": None,
                **release_lease(),
            },
            events=[
                EventSpec(
                    EventType.ERROR, "Worker lease expired.", level=EventLevel.ERROR,
                    data={"code": FailureCode.WORKER_LOST.value},
                )
            ],
        )
        return True
