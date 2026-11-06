"""Handle passed to agent code so it can report progress without touching the store."""

from typing import Any
import logging
import time

from app.services.github import WorkerGitHubGateway
from app.schemas.enums import ArtifactType, EventLevel, EventType, TaskStage
from app.services.transitions import EventSpec, LeaseLost
from app.services.worker_service import WorkerService, CancelRequested


class JobContext:
    def __init__(self, service: WorkerService, task_id: str, worker_id: str, github: WorkerGitHubGateway | None = None):
        self._timing_start = time.perf_counter()
        self._stage_start = self._timing_start
        self._timing_stage = "intake"
        self._first_model = False
        self._docker_runs = 0
        self._stage_docker_runs = 0
        self._stage_model_calls = 0
        self._stage_input_tokens = 0
        self._stage_output_tokens = 0
        self._stage_usage_complete = True
        self.stopped = None
        self.github = github
        self._svc = service
        self.task_id = task_id
        self.worker_id = worker_id

    def checkpoint(self) -> None:
        """Call between graph nodes. Renews the lease; raises on cancel or lost lease."""
        if self.stopped is not None and self.stopped.is_set():
            raise LeaseLost(self.task_id)
        logging.getLogger("gitresolve.worker").debug("checkpoint", extra=self._svc.lease_diagnostics(self.task_id))
        if self._svc.heartbeat(self.task_id, self.worker_id):
            raise CancelRequested(self.task_id)

    def stage(self, stage: TaskStage, **fields: Any) -> None:
        """Enter a stage. Optional fields: baseSha, testCommand, baseline, reproduction."""
        self.checkpoint()
        self.flush_timing()
        self._timing_stage = stage.value
        self._svc.update_progress(self.task_id, self.worker_id, stage=stage, fields=fields or None)

    def flush_timing(self) -> None:
        now = time.perf_counter()
        logging.getLogger("gitresolve.performance").info("stage timing", extra={
            "task_id": self.task_id, "stage": self._timing_stage,
            "duration_ms": round((now - self._stage_start) * 1000, 3), "docker_runs": self._stage_docker_runs,
            "total_docker_runs": self._docker_runs, "model_calls": self._stage_model_calls,
            "input_tokens": self._stage_input_tokens, "output_tokens": self._stage_output_tokens,
            "usage_complete": self._stage_usage_complete})
        self._stage_start = now
        self._stage_docker_runs = self._stage_model_calls = 0
        self._stage_input_tokens = self._stage_output_tokens = 0
        self._stage_usage_complete = True

    def observe_timing(self, record) -> None:
        if record['operation'] == 'docker.run':
            self._docker_runs += 1
            self._stage_docker_runs += 1
        if record['operation'] == 'model.generate':
            self._stage_model_calls += 1
            self._stage_input_tokens += record.get('input_tokens') or 0
            self._stage_output_tokens += record.get('output_tokens') or 0
            self._stage_usage_complete &= record.get('input_tokens') is not None and record.get('output_tokens') is not None

    def model_dispatched(self) -> None:
        if not self._first_model:
            self._first_model = True
            logging.getLogger("gitresolve.performance").info("first model dispatch", extra={
                "task_id": self.task_id, "duration_ms": round((time.perf_counter() - self._timing_start) * 1000, 3)})

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

    def record_lane_state(self, lane_state: dict[str, Any]) -> dict[str, Any]:
        """Persist compact lane decisions, escalations, and oracle summary under lease fencing."""
        if hasattr(self._svc, "record_lane_state"):
            return self._svc.record_lane_state(self.task_id, self.worker_id, lane_state)
        self.checkpoint()
        return dict(lane_state)

    def repository(self):
        """Temporary inert checkout for investigation; never execute code here."""
        from contextlib import contextmanager
        @contextmanager
        def checkout():
            if self.github is None:
                yield None
                return
            import tempfile
            import time
            from pathlib import Path
            from types import SimpleNamespace
            task = self._svc.store.get(f"tasks/{self.task_id}")
            self.checkpoint()
            with tempfile.TemporaryDirectory(prefix="gitresolve-investigate-") as directory:
                request = SimpleNamespace(repo_url=f"https://github.com/{task['repoFullName']}",
                                          commit_sha=task['baseSha'])
                deadline = time.monotonic() + min(60, (task['deadlineAt'] - self._svc.clock()).total_seconds())
                next_check = 0.0
                def cancel():
                    nonlocal next_check
                    if time.monotonic() >= next_check:
                        self.checkpoint()
                        next_check = time.monotonic() + 1.0
                    return False
                path = self.github.source(task)(request, Path(directory), deadline, cancel)
                self.event(EventType.TOOL_CALL, "Pinned GitHub repository snapshot acquired.")
                yield path
                self.checkpoint()
        return checkout()

    def execute(self, *, commit_sha: str, command: list[str], patch: str = ""):
        from app.worker.sandbox import execute
        from githubapp.timing import measure
        with measure("sandbox.execute", task_id=self.task_id):
            return execute(self, commit_sha=commit_sha, command=command, patch=patch)

    def baseline(self, *, commit_sha: str, command: list[str]):
        from app.worker.sandbox import execute
        return execute(self, commit_sha=commit_sha, command=command, baseline=True)


__all__ = ["CancelRequested", "JobContext", "LeaseLost"]
