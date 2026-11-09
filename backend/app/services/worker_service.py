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
from app.core.redaction import redact, redact_value
from app.core.errors import safe_message
from app.services.outcomes import outcome_payload, outcome_for
import logging
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


class CancelRequested(Exception):
    pass


class ExecutionLimit(Exception):
    def __init__(self, code: FailureCode):
        self.code = code
        super().__init__(code.value)

# Structured fields a worker may set through `update_progress`.
PROGRESS_FIELDS = frozenset({
    "baseSha",
    "testCommand",
    "baseline",
    "reproduction",
    "cleanupScope",
    "finalDiffSummary",
    "verification",
    "result",
})
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
    def _owned(self, txn: Txn, task_id: str, worker_id: str, *, stopping: bool = False) -> Ctx:
        try:
            ctx = read_ctx(txn, task_id)
        except Exception:
            raise LeaseLost(task_id) from None
        if ctx.task.get("leaseOwner") != worker_id or not is_active(ctx.task["status"]):
            raise LeaseLost(task_id)
        expires = ctx.task.get("leaseExpiresAt")
        if expires is None or expires <= self.clock():
            raise LeaseLost(task_id)
        if not stopping:
            if ctx.task.get("cancelRequested"):
                raise CancelRequested(task_id)
            if ctx.task.get("deadlineAt") and self.clock() >= ctx.task["deadlineAt"]:
                raise ExecutionLimit(FailureCode.TIMEOUT)
        return ctx

    def lease_diagnostics(self, task_id, failure_count=0):
        try:
            task = self.store.get(f"tasks/{task_id}") or {}
        except Exception:
            task = {}  # logging must never interrupt lease enforcement
        return {"task_id": task_id, "lease_expiry": str(task.get("leaseExpiresAt")),
                "last_heartbeat": str(task.get("heartbeatAt")), "failure_count": failure_count}

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
        if task.get("deadlineAt") and now >= task["deadlineAt"]:
            apply_change(txn, ctx, self.settings, now, to_status=TaskStatus.TIMED_OUT,
                         updates={"dispatchPending": False, "dispatchPhase": None,
                                  "failure": {"code": "timeout", "message": "Task deadline expired before dispatch.",
                                              "phase": phase.value, "retryable": True}})
            return None
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
        updates = {"dispatchPending": False, "outcome": None, **self._lease_updates(now, worker_id)}
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
            ctx = self._owned(txn, task_id, worker_id, stopping=True)
            if ctx.task.get("deadlineAt") and now >= ctx.task["deadlineAt"]:
                raise ExecutionLimit(FailureCode.TIMEOUT)
            # Coalesce rapid checkpoints, never extend a lease only in memory.
            # Every persisted extension is durable before the caller proceeds.
            last = ctx.task.get("heartbeatAt")
            elapsed = (now - last).total_seconds() if last else float("inf")
            if elapsed >= min(self.settings.heartbeat_seconds, self.settings.lease_seconds / 3):
                txn.update(f"tasks/{task_id}", self._lease_updates(now, worker_id))
            return bool(ctx.task.get("cancelRequested"))

        return self.store.run_transaction(fn)

    # --- Progress -----------------------------------------------------------------------
    def record_sandbox(self, task_id, worker_id, receipt):
        """Only the trusted execution bridge records actual Docker evidence.

        Idempotent per (baseSha, diffHash) and fenced by active lease ownership.
        """
        def fn(txn):
            ctx = self._owned(txn, task_id, worker_id)
            existing = ctx.task.get("sandboxVerification")
            new_receipt = dict(receipt)
            base_sha = str(new_receipt.get("baseSha") or "")
            diff_hash = str(new_receipt.get("diffHash") or "")
            if base_sha and diff_hash and not new_receipt.get("receiptId"):
                new_receipt["receiptId"] = (
                    "rcpt_" + sha256_hash(f"{base_sha}:{diff_hash}").removeprefix("sha256:")[:16]
                )
            if isinstance(new_receipt.get("staticOracleReceipt"), dict):
                sor = dict(new_receipt["staticOracleReceipt"])
                if sor.get("baseSha") and sor["baseSha"] != base_sha:
                    raise ValueError("staticOracleReceipt baseSha must match parent receipt.")
                if sor.get("diffHash") and sor["diffHash"] != diff_hash:
                    raise ValueError("staticOracleReceipt diffHash must match parent receipt.")
                sor.setdefault("receiptId", new_receipt.get("receiptId", ""))
                new_receipt["staticOracleReceipt"] = sor

            if (
                isinstance(existing, dict)
                and existing.get("source") == "docker"
                and existing.get("passed") is True
                and new_receipt.get("passed") is True
                and existing.get("baseSha") == base_sha
                and existing.get("diffHash") == diff_hash
            ):
                if existing.get("receiptId"):
                    new_receipt["receiptId"] = existing["receiptId"]
                if existing.get("executionId"):
                    new_receipt["executionId"] = existing["executionId"]
                if isinstance(existing.get("staticOracleReceipt"), dict) and isinstance(
                    new_receipt.get("staticOracleReceipt"), dict
                ):
                    esor = existing["staticOracleReceipt"]
                    if esor.get("verified") is True and esor.get("diffHash") == diff_hash:
                        new_receipt["staticOracleReceipt"] = dict(esor)
                txn.update(f"tasks/{task_id}", {"sandboxVerification": new_receipt})
                return new_receipt

            txn.update(f"tasks/{task_id}", {"sandboxVerification": new_receipt})
            return new_receipt

        return self.store.run_transaction(fn)

    def record_lane_state(self, task_id: str, worker_id: str, lane_state: dict[str, Any]) -> dict[str, Any]:
        """Persist compact lane decisions, escalations, and oracle summary under lease fencing."""
        def fn(txn: Txn) -> dict[str, Any]:
            ctx = self._owned(txn, task_id, worker_id)
            prev = ctx.task.get("laneState") or {}
            if prev.get("lane") == "full" and lane_state.get("lane") == "fast":
                raise ValueError("Monotonic lane invariant violated: cannot transition full -> fast.")
            clean = dict(lane_state)
            txn.update(f"tasks/{task_id}", {"laneState": clean})
            return clean

        return self.store.run_transaction(fn)

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
                if key != "execSeconds" and type(value) is not int:
                    raise ValueError("Call and token counters must be integers.")
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
        clean = redact(content) if type_ in {ArtifactType.FINAL_DIFF, ArtifactType.ATTEMPT_DIFF} else safe_message(content)
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
                updates={"plan": plan_doc, **({"issue": {**ctx.task["issue"], "title": (plan.title or plan.problem_summary).split("\n")[0][:100]}} if ctx.task["issue"].get("number") is None else {}), "dispatchPhase": None, **release_lease()},
                events=[EventSpec(EventType.INFO, "Plan ready for review.")],
            )

        return self.store.run_transaction(fn)

    def complete_execution(
        self, task_id: str, worker_id: str, final_diff: str, final_tests: str,
        summary: dict[str, Any], verification: dict[str, Any] | None = None,
    ) -> Doc:
        """Store the validated diff and move to `awaiting_approval`.

        The hash is computed here from the stored artifact content, so the
        approval hash always matches what the user reviewed.
        """
        now = self.clock()

        def fn(txn: Txn) -> Doc:
            ctx = self._owned(txn, task_id, worker_id)
            cp_path = f"tasks/{task_id}/checkpoints/execute"
            has_cp = txn.get(cp_path) is not None
            diff_doc = self._artifact_doc(ctx.task, "final_diff", ArtifactType.FINAL_DIFF, final_diff, None, now)
            tests_doc = self._artifact_doc(ctx.task, "final_tests", ArtifactType.FINAL_TESTS, final_tests, None, now)
            from app.services.policy import validate_fix
            try:
                from app.services.publication_patch import validate_publication
                validate_publication(final_diff, ctx.task["limits"], verification or {}, ctx.task.get("baseSha"), ctx.task.get("sandboxVerification") or {}, cleanup_scope=ctx.task.get("cleanupScope"))
            except ValueError as exc:
                raise ExecutionLimit(FailureCode.POLICY_VIOLATION) from exc
            if diff_doc["truncated"]:
                raise ValueError("final_diff exceeds the artifact size limit.")
            if self.settings.github_mode == "real":
                receipt = ctx.task.get("sandboxVerification") or {}
                if (receipt.get("source") != "docker" or receipt.get("passed") is not True
                        or receipt.get("diffHash") != sha256_hash(diff_doc["content"])
                        or receipt.get("baseSha") != ctx.task.get("baseSha")
                        or (not ((verification or {}).get("lane") == "fast" and (verification or {}).get("verificationLevel") in {"V0", "V1"} and isinstance(receipt.get("staticOracleReceipt"), dict) and receipt["staticOracleReceipt"].get("verified") is True and receipt["staticOracleReceipt"].get("originalFailed") is True and receipt["staticOracleReceipt"].get("patchedPassed") is True and receipt["staticOracleReceipt"].get("baseSha") == ctx.task.get("baseSha") and receipt["staticOracleReceipt"].get("diffHash") == sha256_hash(diff_doc["content"])) and (type(receipt.get("testsRun")) is not int or receipt["testsRun"] <= 0))):
                    raise ExecutionLimit(FailureCode.POLICY_VIOLATION)
            txn.set(artifact_path(task_id, "final_diff"), diff_doc)
            txn.set(artifact_path(task_id, "final_tests"), tests_doc)
            if has_cp:
                txn.set(cp_path, {"threadId": f"{task_id}:execute", "hasCheckpoint": False, "completed": True})
            clean_summary = dict(summary or {})
            setup_note_hash = None
            if clean_summary.get("setupNote"):
                note_text = redact(str(clean_summary["setupNote"]), 600).strip()[:600]
                setup_note_hash = sha256_hash(note_text)
                clean_summary["setupNote"] = note_text
                clean_summary["setupNoteHash"] = setup_note_hash
            ver_dict = dict(verification or {})
            if setup_note_hash:
                ver_dict["setupNote"] = clean_summary["setupNote"]
                ver_dict["setupNoteHash"] = setup_note_hash
            return apply_change(
                txn, ctx, self.settings, now,
                to_status=TaskStatus.AWAITING_APPROVAL,
                stage=TaskStage.AWAITING_APPROVAL,
                updates={
                    "finalDiffHash": sha256_hash(diff_doc["content"]),
                    "finalDiffSummary": clean_summary,
                    "verification": {key: ver_dict.get(key) for key in
                                     ("reproduced", "regressionPassed", "testsRun", "newFailures", *tuple(k for k in ("lane", "verificationLevel", "oracle", "timing", "escalations", "taskType", "approvedDependencyChanges", "touchedSymbolsCovered", "configProfileValidated", "v2Selection", "placeholderEnv", "setupNote", "setupNoteHash", "resolutionKind") if k in ver_dict))},
                    "approval": {"state": ApprovalState.PENDING.value, "by": None, "at": None, "diffHash": None, "setupNoteHash": None},
                    "dispatchPhase": None,
                    **release_lease(),
                },
                events=[EventSpec(EventType.INFO, "Validated fix ready for review.")],
            )

        return self.store.run_transaction(fn)

    def complete_guidance(
        self,
        task_id: str,
        worker_id: str,
        guidance_outcome: dict[str, Any],
        summary: dict[str, Any] | None = None,
        verification: dict[str, Any] | None = None,
        final_tests: str = "",
    ) -> Doc:
        """Complete a task with external-action setup guidance (`resolved_with_guidance`).

        Terminal non-failure state: no PR is opened, no failure object is stored,
        and the user's active-task slot is released under lease fencing.
        """
        now = self.clock()

        def fn(txn: Txn) -> Doc:
            ctx = self._owned(txn, task_id, worker_id)
            cp_path = f"tasks/{task_id}/checkpoints/execute"
            has_cp = txn.get(cp_path) is not None
            if final_tests:
                tests_doc = self._artifact_doc(
                    ctx.task, "final_tests", ArtifactType.FINAL_TESTS, final_tests, None, now
                )
                txn.set(artifact_path(task_id, "final_tests"), tests_doc)
            if has_cp:
                txn.set(cp_path, {"threadId": f"{task_id}:execute", "hasCheckpoint": False, "completed": True})
            clean_outcome = redact_value(dict(guidance_outcome or {}))
            clean_outcome.setdefault("kind", "needs_user_action")
            clean_outcome.setdefault("reasonCode", "external_secret_missing")
            title_msg = str(clean_outcome.get("title") or "External setup steps ready.")[:200]
            clean_outcome.setdefault("explanation", str(clean_outcome.get("summary") or title_msg))
            clean_outcome.setdefault("nextStep", str(clean_outcome.get("afterYouDoThis") or "Complete the setup steps above."))
            updates: dict[str, Any] = {
                "outcome": clean_outcome,
                "failure": None,
                "result": {
                    **clean_outcome,
                    "outcome": "resolved_with_guidance",
                    "reasonCode": clean_outcome.get("reasonCode", "external_secret_missing"),
                },
                "dispatchPhase": None,
                **release_lease(),
            }
            prev_summary = dict(ctx.task.get("finalDiffSummary") or {})
            prev_ver = dict(ctx.task.get("verification") or {})
            if summary is not None:
                merged_summary = dict(summary)
                for k in ("setupNote", "setupNoteHash", "setupWhatWeFound"):
                    if prev_summary.get(k) and not merged_summary.get(k):
                        merged_summary[k] = prev_summary[k]
                if prev_summary.get("whatWeFound") and not merged_summary.get("setupWhatWeFound"):
                    merged_summary["setupWhatWeFound"] = prev_summary["whatWeFound"]
                updates["finalDiffSummary"] = redact_value(merged_summary)
            if verification is not None:
                merged_ver = dict(verification)
                for k in ("setupNote", "setupNoteHash"):
                    if prev_ver.get(k) and not merged_ver.get(k):
                        merged_ver[k] = prev_ver[k]
                updates["verification"] = redact_value(merged_ver)
            return apply_change(
                txn,
                ctx,
                self.settings,
                now,
                to_status=TaskStatus.RESOLVED_WITH_GUIDANCE,
                updates=updates,
                events=[EventSpec(EventType.INFO, title_msg, level=EventLevel.INFO)],
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
        outcome: str | None = None, evidence=(), next_step: str = "",
    ) -> Doc:
        """Stop the task. The backend, not the agent, maps `code` to a status."""
        message = safe_message(message, 500)
        now = self.clock()
        target = status_for_failure(code)
        evidence = list(evidence or ()) or [a["id"] for a in self.store.list_artifacts(task_id)]

        def fn(txn: Txn) -> Doc:
            ctx = self._owned(txn, task_id, worker_id, stopping=True)
            kind = outcome or outcome_for(code)
            if kind == "infra_retry" and code not in {
                FailureCode.INTERNAL_ERROR, FailureCode.LLM_UNAVAILABLE,
                FailureCode.SANDBOX_ERROR, FailureCode.GITHUB_RATE_LIMITED,
                FailureCode.GITHUB_ERROR,
            }:
                kind = outcome_for(code)  # model output cannot retry policy/budget/access failures
            retries = ctx.task.get("infraRetries", 0)
            if kind == "infra_retry" and phase != DispatchPhase.PR and retries < 2 and not ctx.task.get("cancelRequested") and now < ctx.task["deadlineAt"]:
                return apply_change(txn, ctx, self.settings, now,
                    to_status=TaskStatus.QUEUED if phase == DispatchPhase.PLAN else TaskStatus.RUNNING,
                    updates={"infraRetries": retries + 1, "dispatchPending": True,
                             "dispatchPhase": phase.value, "failure": None,
                             "outcome": outcome_payload(kind, code, message, evidence, next_step),
                             **release_lease()},
                    events=[EventSpec(EventType.INFO, "Temporary infrastructure failure; retry queued.")])
            if kind == "infra_retry":
                kind = "cannot_fix_with_report"
                next_action = "Automatic retries ended. Review the incident reference and run again when infrastructure is available."
            else:
                next_action = next_step
            updates: dict[str, Any] = {
                "outcome": outcome_payload(kind, code, message, evidence, next_action),
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
            ctx = self._owned(txn, task_id, worker_id, stopping=True)
            return apply_change(
                txn, ctx, self.settings, now,
                to_status=TaskStatus.CANCELLED,
                updates={"dispatchPhase": None, **release_lease()},
                events=[EventSpec(EventType.CANCELLATION, "Task cancelled.")],
            )

        return self.store.run_transaction(fn)

    # --- Reaper ---------------------------------------------------------------------------
    def _has_resumable_checkpoint(self, txn: Txn, task_id: str, phase: str) -> bool:
        cp_doc = txn.get(f"tasks/{task_id}/checkpoints/{phase}")
        if isinstance(cp_doc, dict) and cp_doc.get("hasCheckpoint") and not cp_doc.get("deleted"):
            return True
        return False

    def reap_expired(self) -> list[str]:
        """Requeue expired execute leases with a valid checkpoint, or mark `worker_lost`."""
        now = self.clock()
        reaped: list[str] = []
        for candidate in self.store.list_expired_leases(now, 50):
            logging.getLogger("gitresolve.worker").warning("expired lease observed", extra=self.lease_diagnostics(candidate["id"]))
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
        max_crash_resumes = int((task.get("limits") or {}).get("maxCrashResumes", 2))
        crash_resumes = int(task.get("crashResumes", 0))
        has_cp = phase == DispatchPhase.EXECUTE and self._has_resumable_checkpoint(txn, task_id, phase.value)
        can_resume = (
            has_cp
            and not task.get("cancelRequested")
            and (not task.get("deadlineAt") or now < task["deadlineAt"])
            and crash_resumes < max_crash_resumes
        )
        if can_resume:
            apply_change(
                txn, ctx, self.settings, now,
                to_status=TaskStatus.RUNNING,
                updates={
                    "crashResumes": crash_resumes + 1,
                    "dispatchPending": True,
                    "dispatchPhase": phase.value,
                    "failure": None,
                    **release_lease(),
                },
                events=[
                    EventSpec(
                        EventType.INFO,
                        "Worker lease expired; requeued to resume from the last verified checkpoint.",
                        level=EventLevel.WARN,
                        data={
                            "kind": "crash_resume",
                            "resumeCount": crash_resumes + 1,
                            "phase": phase.value,
                        },
                    )
                ],
            )
            return True

        exhausted_updates: dict[str, Any] = {}
        if has_cp and crash_resumes >= max_crash_resumes:
            exhausted_updates["outcome"] = outcome_payload(
                "cannot_fix_with_report",
                FailureCode.WORKER_LOST,
                "The worker stopped responding after automatic checkpoint resume attempts. Run the task again.",
                [],
                "Automatic checkpoint resumes ended. Review the incident reference and run the task again.",
            )
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
                **exhausted_updates,
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
