import pytest

from app.demo import run_demo
from app.schemas.tasks import PlanData
from app.services.worker_service import ExecutionLimit
from tests.integration.test_task_lifecycle import drive_to_awaiting_approval


def test_frontend_free_real_graph_worker_demo():
    result = run_demo(mock_llm=True)
    assert result["submitted"] == 202
    assert result["status"] == "awaiting_start"
    assert result["events"] > 0 and "plan" in result["artifacts"]
    assert result["pr_created"] is False


def test_completion_checks_deadline_inside_transaction(env):
    tid = env.create(env.headers()).json()["id"]
    env.worker_service.claim_next("w1")
    env.store.run_transaction(lambda tx: tx.update(f"tasks/{tid}", {"deadlineAt": env.clock()}))
    with pytest.raises(ExecutionLimit):
        env.worker_service.complete_plan(tid, "w1", PlanData(problem_summary="x", steps=["x"]))
    assert env.store.get(f"tasks/{tid}/artifacts/plan") is None


def test_expired_queued_task_never_invokes_agent(env, monkeypatch):
    tid = env.create(env.headers()).json()["id"]
    env.clock.advance(seconds=1201)
    monkeypatch.setattr(env.agent, "plan", lambda *args: pytest.fail("expired work executed"))
    assert not env.run_worker()
    assert env.store.get(f"tasks/{tid}")["status"] == "timed_out"


def test_human_approval_starts_new_pr_deadline(env):
    h = env.headers()
    task = drive_to_awaiting_approval(env, h)
    env.clock.advance(hours=2)
    assert env.post(h, task["id"], "approve", {"diffHash": task["finalDiffHash"]}).status_code == 200
    env.run_worker()
    assert env.get(h, task["id"])["status"] == "completed"
