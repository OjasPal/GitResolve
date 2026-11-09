"""Failure injection: these demonstrate possible worker_lost causes, not its historic cause."""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from app.agent.interface import PlanResult
from app.core.errors import PersistenceError, public_error
from app.core.redaction import redact, redact_value
from app.schemas.enums import ArtifactType, DispatchPhase, EventType, FailureCode
from app.services.outcomes import OUTCOME_CODES
from app.services.store.memory import MemoryStore
from app.services.transitions import EventSpec, LeaseLost, event_path
from app.worker.context import CancelRequested, JobContext
from app.worker.runner import _HeartbeatThread


@pytest.mark.parametrize("path", [r"C:\Users\Ojas Pal\GitResolve\.diagnostics\local-state.json",
    r"C:\\Users\\Ojas Pal\\AppData\\Local\\Temp\\state", "/home/user/private", "/Users/ojas/private", "/tmp/secret", "/private/var/folders/temp/file"])
def test_host_paths_and_credentials(path):
    text = f"PermissionError: {path} password=supersecret Bearer abc.def.ghi"
    clean = redact(text)
    assert path not in clean and "supersecret" not in clean and "abc.def.ghi" not in clean
    error = public_error(PermissionError(text))
    assert error.category == "storage_unavailable" and error.incident_id in error.message
    assert "PermissionError" not in error.message and path not in error.message


def test_diagnostics_relative_paths_and_urls_survive():
    text = "tests/test_app.py:19 AssertionError: expected 2, got 1 https://github.com/OjasPal/GitResolve"
    assert redact(text) == text
    assert redact_value({"password": "tiny", "nested": ["token=abcdefghi"]}) == {"password": "[REDACTED]", "nested": ["[REDACTED]"]}


def test_replace_transient_windows_denial(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path / "local-state.json")
    original = os.replace
    attempts = []
    def replace(src, dst):
        attempts.append(1)
        if len(attempts) < 3:
            raise PermissionError(5, "Access denied", str(src), None, str(dst))
        return original(src, dst)
    monkeypatch.setattr("app.services.store.memory.os.replace", replace)
    monkeypatch.setattr("app.services.store.memory.time.sleep", lambda delay: None)
    store.set("tasks/t", {"leaseOwner": "worker", "heartbeatAt": "now"})
    assert len(attempts) == 3
    assert store.get("tasks/t")["leaseOwner"] == "worker"
    assert not list(tmp_path.glob(".gitresolve-state-*"))
    store.close()
    restored = MemoryStore(tmp_path / "local-state.json")
    assert restored.get("tasks/t")["heartbeatAt"] == "now"
    restored.close()


def test_replace_persistent_denial_rolls_back_transaction(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path / "local-state.json")
    store.set("tasks/t", {"leaseOwner": "before"})
    before = (tmp_path / "local-state.json").read_bytes()
    attempts = []
    def denied(*args):
        attempts.append(1)
        raise PermissionError(r"C:\Users\private\local-state.json")
    monkeypatch.setattr("app.services.store.memory.os.replace", denied)
    monkeypatch.setattr("app.services.store.memory.time.sleep", lambda delay: None)
    with pytest.raises(PersistenceError) as caught:
        store.run_transaction(lambda tx: tx.update("tasks/t", {"leaseOwner": "after"}))
    assert len(attempts) == 5 and "Users" not in str(caught.value)
    assert store.get("tasks/t")["leaseOwner"] == "before"
    assert (tmp_path / "local-state.json").read_bytes() == before
    assert not list(tmp_path.glob(".gitresolve-state-*"))
    store.close()


def test_state_owner_blocks_second_process_and_releases(tmp_path):
    path = tmp_path / "state.json"
    store = MemoryStore(path)
    script = "from app.services.store.memory import MemoryStore; import sys; MemoryStore(sys.argv[1])"
    environment = {**os.environ, "PYTHONPATH": str(Path("backend").resolve())}
    result = subprocess.run([sys.executable, "-c", script, str(path)], env=environment, capture_output=True, text=True)
    assert result.returncode != 0 and "already owned" in result.stderr
    store.close()
    result = subprocess.run([sys.executable, "-c", script, str(path)], env=environment, capture_output=True, text=True)
    assert result.returncode == 0


def claimed(env):
    h = env.headers()
    tid = env.create(h).json()["id"]
    job = env.worker_service.claim_next("w1")
    return h, tid, job


def test_all_public_boundaries(env):
    h, tid, job = claimed(env)
    text = r"Failure at C:\Users\Ojas Pal\private.txt" + " password=supersecret"
    ctx = JobContext(env.worker_service, tid, "w1")
    ctx.event(EventType.INFO, text, data={"model": text})
    ctx.artifact("attempt_001_tests", ArtifactType.ATTEMPT_TESTS, text + "\ntests/test_app.py:19 AssertionError: expected 2")
    env.worker_service.fail(tid, "w1", FailureCode.INTERNAL_ERROR, text, DispatchPhase.PLAN)
    for suffix in ["", "/events", "/artifacts/attempt_001_tests"]:
        response = env.client.get(f"/api/v1/tasks/{tid}{suffix}", headers=h)
        assert response.status_code == 200
        assert "Users" not in response.text and "supersecret" not in response.text
    content = env.client.get(f"/api/v1/tasks/{tid}/artifacts/attempt_001_tests", headers=h).json()["content"]
    assert "tests/test_app.py:19 AssertionError" in content


@pytest.mark.parametrize("kind,status", [("needs_user_action", "failed_infra"), ("needs_clarification", "needs_input"),
    ("cannot_reproduce", "failed_not_reproduced"), ("out_of_scope", "failed_policy"), ("cannot_fix_with_report", "failed_no_fix")])
def test_outcomes_status_mapping(env, kind, status):
    h, tid, job = claimed(env)
    env.worker_service.fail(tid, "w1", OUTCOME_CODES[kind], "Safe explanation", DispatchPhase.PLAN,
                            outcome=kind, evidence=["tests/test_app.py"], next_step="Review evidence.")
    task = env.get(h, tid)
    assert task["status"] == status
    assert task["outcome"]["kind"] == kind and task["outcome"]["evidence"]
    assert task["userMessage"] == "Safe explanation"


def test_infra_retry_is_bounded_and_keeps_active_slot(env):
    h, tid, job = claimed(env)
    for attempt in range(3):
        env.worker_service.fail(tid, "w1", FailureCode.INTERNAL_ERROR, "Storage unavailable.",
                                DispatchPhase.PLAN, outcome="infra_retry")
        task = env.get(h, tid)
        if attempt < 2:
            assert task["status"] == "queued" and task["outcome"]["kind"] == "infra_retry"
            assert env.store.get("users/1001")["activeTaskId"] == tid
            assert env.worker_service.claim_next("w1") is not None
        else:
            assert task["status"] == "failed_infra"
            assert task["outcome"]["kind"] != "infra_retry"
            assert env.worker_service.claim_next("w1") is None


def test_heartbeat_coalescing_and_expired_lease_cannot_revive(env, monkeypatch):
    h, tid, job = claimed(env)
    writes = []
    original = env.store._persist
    monkeypatch.setattr(env.store, "_persist", lambda docs: writes.append(1))
    before = env.store.get(f"tasks/{tid}")["leaseExpiresAt"]
    for _ in range(20):
        env.worker_service.heartbeat(tid, "w1")
    assert not writes
    assert env.store.get(f"tasks/{tid}")["leaseExpiresAt"] == before
    env.clock.advance(seconds=env.settings.lease_seconds + 1)
    with pytest.raises(LeaseLost):
        JobContext(env.worker_service, tid, "w1").checkpoint()
    assert env.worker_service.reap_expired() == [tid]
    assert env.get(h, tid)["status"] == "worker_lost"


def test_heartbeat_transient_failure_then_recovery(env, monkeypatch):
    h, tid, job = claimed(env)
    beat = _HeartbeatThread(env.worker_service, tid, "w1", 0)
    real = env.worker_service.heartbeat
    calls = []
    def heartbeat(*args):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError("transient denial")
        beat.stop()
        return real(*args)
    monkeypatch.setattr(env.worker_service, "heartbeat", heartbeat)
    beat.run()
    assert len(calls) == 2 and not beat.stopped.is_set()
    assert beat.failure_count == 0


def test_persistent_heartbeat_failure_expires_and_stops_agent(env, monkeypatch):
    h, tid, job = claimed(env)
    beat = _HeartbeatThread(env.worker_service, tid, "w1", 0)
    def heartbeat(*args):
        env.clock.advance(seconds=env.settings.lease_seconds + 1)
        raise PermissionError("durable heartbeat unavailable")
    monkeypatch.setattr(env.worker_service, "heartbeat", heartbeat)
    beat.run()
    assert beat.stopped.is_set()
    ctx = JobContext(env.worker_service, tid, "w1")
    ctx.stopped = beat.stopped
    with pytest.raises(LeaseLost):
        ctx.checkpoint()
    assert env.worker_service.reap_expired() == [tid]


def test_cancel_checkpoint(env):
    h, tid, job = claimed(env)
    env.post(h, tid, "cancel")
    with pytest.raises(CancelRequested):
        JobContext(env.worker_service, tid, "w1").checkpoint()


def test_durable_heartbeat_throttle_preserves_disk_lease(env, tmp_path, monkeypatch):
    h, tid, job = claimed(env)
    store = MemoryStore(tmp_path / "state.json")
    for path, doc in env.store._docs.items():
        store.set(path, doc)
    env.worker_service.store = store
    original = store._persist
    writes = []
    def persist(docs):
        writes.append(1)
        original(docs)
    monkeypatch.setattr(store, "_persist", persist)
    for _ in range(10):
        env.worker_service.heartbeat(tid, "w1")
    assert writes == []
    env.clock.advance(seconds=env.settings.heartbeat_seconds)
    env.worker_service.heartbeat(tid, "w1")
    assert writes == [1]
    lease = store.get(f"tasks/{tid}")["leaseExpiresAt"]
    store.close()
    restored = MemoryStore(tmp_path / "state.json")
    assert restored.get(f"tasks/{tid}")["leaseExpiresAt"] == lease
    restored.close()


def test_raw_worker_exception_never_reaches_api(env, monkeypatch):
    h = env.headers()
    tid = env.create(h).json()["id"]
    def fail(*args):
        raise RuntimeError("internal database layout details password=supersecret")
    monkeypatch.setattr(env.agent, "plan", fail)
    env.run_worker()
    task = env.get(h, tid)
    assert "internal database layout" not in json.dumps(task)
    assert "supersecret" not in json.dumps(task)
    assert "Reference:" in task["failure"]["message"]


def test_final_diff_with_host_data_is_rejected_without_mutation(env):
    h, tid, job = claimed(env)
    with pytest.raises(ValueError):
        env.worker_service.save_artifact(tid, "w1", "final_diff", ArtifactType.FINAL_DIFF,
                                        r"+path = 'C:\Users\private\file'")
    assert env.store.get(f"tasks/{tid}/artifacts/final_diff") is None


def test_policy_cannot_be_automatically_retried(env):
    h, tid, job = claimed(env)
    env.worker_service.fail(tid, "w1", FailureCode.POLICY_VIOLATION, "Policy denied.",
                            DispatchPhase.PLAN, outcome="infra_retry")
    assert env.get(h, tid)["status"] == "failed_policy"
    assert env.worker_service.claim_next("w1") is None


@pytest.mark.parametrize("text,secret", [('{"password": "tiny"}', "tiny"),
    ("GITHUB_TOKEN=privatevalue", "privatevalue"), ("GOOGLE_API_KEY=privatevalue", "privatevalue")])
def test_quoted_and_environment_credentials(text, secret):
    assert secret not in redact(text)


def test_legacy_events_are_sanitized_on_read(env):
    h, tid, job = claimed(env)
    env.worker_service.append_event(tid, "w1", EventSpec(EventType.INFO, "placeholder"))
    rows = env.store.list_events(tid, 0, 50)
    event = rows[-1]
    event["message"] = r"PermissionError: C:\Users\private\state.json password=secretvalue"
    env.store.set(event_path(tid, event["seq"]), event)
    response = env.client.get(f"/api/v1/tasks/{tid}/events", headers=h)
    assert response.status_code == 200
    assert "private" not in response.text and "PermissionError" not in response.text


def test_transaction_read_after_write_prevented(env):
    def bad_txn(txn):
        txn.set("tasks/dummy", {"a": 1})
        return txn.get("tasks/dummy")

    with pytest.raises(PersistenceError, match="ReadAfterWriteError"):
        env.store.run_transaction(bad_txn)

    h, tid, job = claimed(env)
    base_sha = "a" * 40
    env.store.run_transaction(lambda tx: tx.update(f"tasks/{tid}", {"baseSha": base_sha, "status": "running"}))
    env.store.set(f"tasks/{tid}/checkpoints/execute", {"threadId": f"{tid}:execute", "hasCheckpoint": True})
    diff = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n"
    verification = {"lane": "fast", "verificationLevel": "V1", "reproduced": True, "regressionPassed": True, "testsRun": 1, "newFailures": []}
    env.worker_service.complete_execution(tid, "w1", diff, "def test_ok(): pass", {"summary": "fixed"}, verification=verification)
    task = env.get(h, tid)
    assert task["status"] == "awaiting_approval"
    cp = env.store.get(f"tasks/{tid}/checkpoints/execute")
    assert cp["hasCheckpoint"] is False and cp["completed"] is True

    # Verify complete_guidance does not perform read after write with checkpoint present
    h2, tid2, job2 = claimed(env)
    env.store.run_transaction(lambda tx: tx.update(f"tasks/{tid2}", {"status": "running"}))
    env.store.set(f"tasks/{tid2}/checkpoints/execute", {"threadId": f"{tid2}:execute", "hasCheckpoint": True})
    env.worker_service.complete_guidance(tid2, "w1", {"title": "Setup needed"}, final_tests="def test_g(): pass")
    task2 = env.get(h2, tid2)
    assert task2["status"] == "resolved_with_guidance"
    cp2 = env.store.get(f"tasks/{tid2}/checkpoints/execute")
    assert cp2["hasCheckpoint"] is False and cp2["completed"] is True
