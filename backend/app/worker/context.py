"""Handle passed to agent code so it can report progress without touching the store."""

from typing import Any

from app.schemas.enums import ArtifactType, EventLevel, EventType, TaskStage
from app.services.transitions import EventSpec, LeaseLost
from app.services.worker_service import WorkerService, CancelRequested


class JobContext:
    def __init__(self, service: WorkerService, task_id: str, worker_id: str):
        self._svc = service
        self.task_id = task_id
        self.worker_id = worker_id

    def checkpoint(self) -> None:
        """Call between graph nodes. Renews the lease; raises on cancel or lost lease."""
        if self._svc.heartbeat(self.task_id, self.worker_id):
            raise CancelRequested(self.task_id)

    def stage(self, stage: TaskStage, **fields: Any) -> None:
        """Enter a stage. Optional fields: baseSha, testCommand, baseline, reproduction."""
        self.checkpoint()
        self._svc.update_progress(self.task_id, self.worker_id, stage=stage, fields=fields or None)

    def event(
        self, type_: EventType, message: str, level: EventLevel = EventLevel.INFO,
        data: dict[str, Any] | None = None,
    ) -> None:
        self._svc.append_event(
            self.task_id, self.worker_id, EventSpec(type_, message, level, data or {})
        )

    def artifact(
        self, artifact_id: str, type_: ArtifactType, content: str, attempt: int | None = None
    ) -> None:
        self._svc.save_artifact(self.task_id, self.worker_id, artifact_id, type_, content, attempt)

    def usage(self, attempts_used: int | None = None, **budgets: Any) -> None:
        """Flush counters at stage boundaries: modelCalls, inputTokens, outputTokens, execSeconds."""
        self._svc.update_progress(
            self.task_id, self.worker_id, attempts_used=attempts_used, budgets=budgets or None
        )

    def execute(self, *, commit_sha: str, command: list[str], patch: str = ""):
        from app.worker.sandbox import execute
        return execute(self, commit_sha=commit_sha, command=command, patch=patch)


__all__ = ["CancelRequested", "JobContext", "LeaseLost"]
