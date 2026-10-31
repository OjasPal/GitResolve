"""Contract tests use the real sandbox models when both branches are present."""
import importlib.util

import pytest

from app.services.worker_service import CancelRequested
from app.worker.context import JobContext
from app.worker.sandbox import execute


def running_context(env):
    headers = env.headers()
    tid = env.create(headers).json()["id"]
    env.run_worker()
    env.post(headers, tid, "start")
    assert env.worker_service.claim_next("w1") is not None
    return JobContext(env.worker_service, tid, "w1")


@pytest.fixture
def sandbox_modules():
    try:
        spec = importlib.util.find_spec("sandbox.runner")
    except ModuleNotFoundError:
        spec = None
    if spec is None:
        pytest.skip("Combined checkout with Member 3's sandbox is required.")
    from sandbox import models, runner
    return models, runner


def test_bridge_maps_task_patch_and_cumulative_usage(env, monkeypatch, sandbox_modules):
    models, runner = sandbox_modules
    ctx = running_context(env)
    ctx.usage(execSeconds=2)
    requests = []
    def run(request, cancel):
        requests.append(request)
        assert not cancel()
        return models.ExecutionResult(execution_id=request.execution_id, status="passed", exit_code=0,
                                      commit_sha=request.commit_sha, duration_ms=1500, stdout="1 passed")
    monkeypatch.setattr(runner, "run_sandbox", run)
    result = execute(ctx, commit_sha="a" * 40, command=["python", "-m", "pytest"], patch="a patch")
    request = requests[0]
    assert request.execution_id == ctx.task_id and request.patch == "a patch"
    assert request.timeout_seconds == 300 and request.repo_url.startswith("https://github.com/")
    assert result.stdout == "1 passed"
    assert env.store.get(f"tasks/{ctx.task_id}")["budgets"]["execSeconds"] == 3.5


def test_bridge_stops_on_cancellation(env, monkeypatch, sandbox_modules):
    models, runner = sandbox_modules
    ctx = running_context(env)
    def run(request, cancel):
        env.app.state.task_service.cancel_task("1001", ctx.task_id)
        assert cancel()
        return models.ExecutionResult(execution_id=ctx.task_id, status="error", error_code="cancelled")
    monkeypatch.setattr(runner, "run_sandbox", run)
    with pytest.raises(CancelRequested):
        execute(ctx, commit_sha="a" * 40, command=["python"])


def test_bridge_rejects_failed_cleanup(env, monkeypatch, sandbox_modules):
    models, runner = sandbox_modules
    ctx = running_context(env)
    monkeypatch.setattr(runner, "run_sandbox", lambda request, cancel:
                        models.ExecutionResult(execution_id=ctx.task_id, status="error", cleanup_ok=False))
    with pytest.raises(RuntimeError, match="cleanup"):
        execute(ctx, commit_sha="a" * 40, command=["python"])
