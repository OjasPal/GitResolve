"""Regression coverage for review-time integrity and cancellation findings."""

import pytest

from app.agent.interface import ExecuteResult
from app.services.transitions import LeaseLost
from app.services.worker_service import ExecutionLimit
from app.services.transitions import EventSpec
from app.schemas.enums import EventType
from tests.integration.test_task_lifecycle import drive_to_awaiting_approval


def test_usage_limits_and_metadata_boundary(env):
    task_id = env.create(env.headers()).json()["id"]
    env.worker_service.claim_next("w1")
    with pytest.raises(ExecutionLimit):
        env.worker_service.update_progress(task_id, "w1", budgets={"modelCalls": 999})
    with pytest.raises(ValueError):
        env.worker_service.update_progress(task_id, "w1", budgets={"execSeconds": float("nan")})
    env.worker_service.append_event(task_id, "w1", EventSpec(EventType.INFO, "test", data={
        "token": "sensitive-marker", "source": "private source", "ran": 3,
    }))
    assert env.store.list_events(task_id, 0, 50)[-1]["data"] == {"ran": 3}


def test_deadline_stops_worker_and_releases_slot(env, monkeypatch):
    h = env.headers()
    task_id = env.create(h).json()["id"]
    original = env.agent.plan
    def plan(task, ctx):
        result = original(task, ctx)
        env.store.run_transaction(lambda txn: txn.update(f"tasks/{task_id}", {"deadlineAt": env.clock()}))
        return result
    monkeypatch.setattr(env.agent, "plan", plan)
    env.run_worker()
    assert env.get(h, task_id)["status"] == "timed_out"
    assert env.create(h).status_code == 202


@pytest.mark.parametrize("overdue", [0, 1])
def test_expired_worker_cannot_renew_or_publish_and_reaper_frees_slot(env, overdue):
    headers = env.headers()
    task_id = env.create(headers).json()["id"]
    env.worker_service.claim_next("w1")
    env.clock.advance(seconds=env.settings.lease_seconds + overdue)
    with pytest.raises(LeaseLost):
        env.worker_service.heartbeat(task_id, "w1")
    with pytest.raises(LeaseLost):
        env.worker_service.update_progress(task_id, "w1", budgets={"modelCalls": 1})
    assert env.worker_service.reap_expired() == [task_id]
    assert env.get(headers, task_id)["status"] == "worker_lost"
    assert env.create(headers).status_code == 202


@pytest.mark.parametrize("phase", ["plan", "execute"])
def test_cancel_during_last_agent_call_is_honored(env, monkeypatch, phase):
    headers = env.headers()
    task_id = env.create(headers).json()["id"]
    if phase == "execute":
        env.run_worker()
        env.post(headers, task_id, "start")
    original = getattr(env.agent, phase)

    def cancel_before_return(task, ctx):
        result = original(task, ctx)
        env.app.state.task_service.cancel_task("1001", task_id)
        return result

    monkeypatch.setattr(env.agent, phase, cancel_before_return)
    env.run_worker()
    assert env.get(headers, task_id)["status"] == "cancelled"


def test_diff_requiring_redaction_is_rejected_not_rewritten(env, monkeypatch):
    headers = env.headers()
    task_id = env.create(headers).json()["id"]
    env.run_worker()
    env.post(headers, task_id, "start")
    diff = '--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-password="oldvalue"\n+password="newvalue"\n'
    monkeypatch.setattr(env.agent, "execute", lambda task, ctx: ExecuteResult(
        "fix_ready", final_diff=diff, final_tests="passed"
    ))
    env.run_worker()
    task = env.get(headers, task_id)
    assert task["status"] == "failed_infra"
    assert task["finalDiffHash"] is None
    assert env.store.get(f"tasks/{task_id}/artifacts/final_diff") is None


@pytest.mark.parametrize("tamper", ["content", "approval"])
def test_pr_creator_is_not_called_when_approval_integrity_is_broken(env, monkeypatch, tamper):
    headers = env.headers()
    task = drive_to_awaiting_approval(env, headers)
    task_id = task["id"]
    env.post(headers, task_id, "approve", {"diffHash": task["finalDiffHash"]})
    path = f"tasks/{task_id}"
    if tamper == "content":
        path += "/artifacts/final_diff"
        doc = env.store.get(path)
        doc["content"] += "changed after approval\n"
    else:
        doc = env.store.get(path)
        doc["approval"]["state"] = "pending"
    env.store.set(path, doc)
    calls = []
    monkeypatch.setattr(env.pr_creator, "create", lambda *args: calls.append(args))
    env.run_worker()
    assert calls == []
    assert env.get(headers, task_id)["status"] == "failed_infra"
