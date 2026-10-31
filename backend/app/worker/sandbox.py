"""Lazy adapter for Member 3's sandbox, called only by trusted agent nodes."""

import importlib
import math
import sys
import time
from pathlib import Path

from app.schemas.enums import FailureCode
from app.services.worker_service import ExecutionLimit


def execute(ctx, *, commit_sha: str, command: list[str], patch: str = ""):
    """Execute pinned public repository code with the task's remaining budget.

    Returns ExecutionResult. A passing process is not proof of a fixed bug;
    the agent must compare baseline, reproduction and final test evidence.
    """
    ctx.checkpoint()
    task = ctx._svc.store.get(f"tasks/{ctx.task_id}")
    remaining = min(
        (task["deadlineAt"] - ctx._svc.clock()).total_seconds(),
        task["limits"]["taskTimeoutSeconds"] - task["budgets"]["execSeconds"],
        task["limits"]["executionTimeoutSeconds"], 300,
    )
    if remaining < 1:
        raise ExecutionLimit(FailureCode.TIMEOUT)
    root = next((p for p in Path(__file__).resolve().parents if (p / "sandbox" / "runner.py").is_file()), None)
    if root is None:
        raise RuntimeError("Sandbox package is unavailable; integrate the sandbox feature branch.")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    models = importlib.import_module("sandbox.models")
    runner = importlib.import_module("sandbox.runner")
    request = models.ExecutionRequest(
        execution_id=ctx.task_id, repo_url=f"https://github.com/{task['repoFullName']}",
        commit_sha=commit_sha, command=command, timeout_seconds=math.floor(remaining), patch=patch,
    )
    stopped = []
    next_check = 0.0
    def cancel():
        nonlocal next_check
        if stopped:
            return True
        if time.monotonic() < next_check:
            return False
        try:
            ctx.checkpoint()
            next_check = time.monotonic() + 1.0
            return False
        except Exception as exc:
            stopped.append(exc)
            return True  # store outages must stop execution too
    result = runner.run_sandbox(request, cancel=cancel)
    if stopped:
        raise stopped[0]
    if not result.cleanup_ok:
        raise RuntimeError(result.error_message or "Sandbox cleanup failed.")
    ctx.checkpoint()
    ctx.usage(execSeconds=task["budgets"]["execSeconds"] + result.duration_ms / 1000)
    return result
