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
    kwargs = {}
    source_errors = []
    if ctx.github is not None:
        provider = ctx.github.source(task)
        def source(*args):
            try:
                path = provider(*args)
                from app.schemas.enums import EventType
                ctx.event(EventType.TOOL_CALL, "Pinned GitHub snapshot acquired for sandbox execution.")
                return path
            except Exception as exc:
                source_errors.append(exc)
                raise
        kwargs["source_provider"] = source
    result = runner.run_sandbox(request, cancel=cancel, **kwargs)
    if stopped:
        raise stopped[0]
    if not result.cleanup_ok:
        raise RuntimeError(result.error_message or "Sandbox cleanup failed.")
    if source_errors:
        raise source_errors[0]
    ctx.checkpoint()
    ctx.usage(execSeconds=task["budgets"]["execSeconds"] + result.duration_ms / 1000)
    import re
    from app.services.ids import sha256_hash
    ctx._svc.record_sandbox(ctx.task_id, ctx.worker_id, {
        "source": "docker", "baseSha": commit_sha, "diffHash": sha256_hash(patch),
        "command": command, "passed": result.status == "passed" and result.exit_code == 0
            and not result.oom_killed and result.cleanup_ok,
        "executionId": result.execution_id,
        "testsRun": sum(int(n) for n in re.findall(r"\b(\d+) passed\b", result.stdout))
                    if command[:3] == ["python", "-m", "pytest"]
                    and not any(arg in {"--collect-only", "--co", "--version", "--help", "-h"} for arg in command) else 0,
    })
    return result
