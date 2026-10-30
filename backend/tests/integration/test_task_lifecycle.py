"""End-to-end lifecycle through the HTTP API plus the worker, with no real agent."""

import threading

import pytest

from app.schemas.enums import EventLevel, EventType, FailureCode
from app.services.transitions import EventSpec, LeaseLost
from app.worker.pr import PullRequestError
from tests.conftest import API, REPO_ID

INTERNAL_KEYS = {
    "installationId", "dispatchPending", "dispatchPhase", "dispatchAt", "leaseOwner",
    "leaseExpiresAt", "heartbeatAt", "expiresAt", "userId", "idempotencyKey", "eventSeq",
    "schemaVersion", "testCommand",
}


def active_task(env, h):
    return env.client.get(f"{API}/me", headers=h).json()["activeTaskId"]


def drive_to_awaiting_approval(env, h, issue=1) -> dict:
    task_id = env.create(h, issue).json()["id"]
    assert env.run_worker()  # plan
    assert env.post(h, task_id, "start").status_code == 200
    assert env.run_worker()  # execute
    return env.get(h, task_id)


# --- Happy path ------------------------------------------------------------------
def test_full_lifecycle_without_the_real_agent(env):
    h = env.headers()
    r = env.create(h)
    assert r.status_code == 202
    task = r.json()
    task_id = task["id"]
    assert task["status"] == "queued" and task["stage"] == "intake"
    assert task["attempts"] == {"used": 0, "max": 3}
    assert task["availableActions"] == ["cancel"]
    assert INTERNAL_KEYS.isdisjoint(task) and "userId" not in task
    assert active_task(env, h) == task_id

    assert env.run_worker()
    task = env.get(h, task_id)
    assert task["status"] == "awaiting_start" and task["stage"] == "plan"
    assert task["plan"]["steps"] and task["availableActions"] == ["start", "cancel"]
    assert active_task(env, h) is None  # human gate releases the slot

    assert env.post(h, task_id, "start").status_code == 200
    assert env.get(h, task_id)["status"] == "running"
    assert active_task(env, h) == task_id

    assert env.run_worker()
    task = env.get(h, task_id)
    assert task["status"] == "awaiting_approval" and task["stage"] == "awaiting_approval"
    assert task["finalDiffHash"].startswith("sha256:")
    assert task["attempts"]["used"] == 1 and task["reproduction"]["outcome"] == "reproduced"
    assert task["availableActions"] == ["approve", "reject"] and task["pr"] is None

    arts = env.client.get(f"{API}/tasks/{task_id}/artifacts", headers=h).json()["items"]
    assert {a["id"] for a in arts} == {"plan", "final_diff", "final_tests"}
    assert all("content" not in a for a in arts)
    diff = env.client.get(f"{API}/tasks/{task_id}/artifacts/final_diff", headers=h).json()
    assert diff["content"].startswith("--- a/")

    r = env.post(h, task_id, "approve", {"diffHash": task["finalDiffHash"]})
    assert r.status_code == 200 and r.json()["status"] == "creating_pr"
    assert r.json()["approval"]["state"] == "approved"
    assert env.run_worker()
    task = env.get(h, task_id)
    assert task["status"] == "completed" and task["pr"]["number"] == 1
    assert active_task(env, h) is None
    assert task["availableActions"] == ["run_again"]

    events = env.client.get(f"{API}/tasks/{task_id}/events?limit=50", headers=h).json()
    seqs = [e["seq"] for e in events["items"]]
    assert seqs == sorted(seqs) == list(range(1, len(seqs) + 1))
    assert events["nextAfter"] == seqs[-1]
    assert not env.run_worker()  # nothing left to dispatch


def test_events_pagination_and_cross_user_isolation(env):
    a, b = env.headers("1"), env.headers("2")
    task_id = env.create(a).json()["id"]
    env.run_worker()
    page1 = env.client.get(f"{API}/tasks/{task_id}/events?limit=2", headers=a).json()
    page2 = env.client.get(f"{API}/tasks/{task_id}/events?after={page1['nextAfter']}&limit=50", headers=a).json()
    assert len(page1["items"]) == 2
    assert page2["items"][0]["seq"] == page1["nextAfter"] + 1
    missing = env.client.get(f"{API}/tasks/tsk_{'0' * 20}", headers=a)
    for path in ("", "/events", "/artifacts", "/artifacts/plan"):
        foreign = env.client.get(f"{API}/tasks/{task_id}{path}", headers=b)
        assert foreign.status_code == 404
        assert foreign.json() == missing.json()  # indistinguishable from a nonexistent task
    for action in ("start", "cancel", "reject"):
        assert env.post(b, task_id, action).status_code == 404


# --- Active slot, quotas, idempotency ---------------------------------------------------
def test_one_active_task_per_user(env):
    h = env.headers()
    first = env.create(h, 1).json()["id"]
    r = env.create(h, 2)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "active_task_exists"
    assert r.json()["error"]["details"]["activeTaskId"] == first

    env.run_worker()  # first reaches awaiting_start -> slot free
    second = env.create(h, 2)
    assert second.status_code == 202
    # Starting the first now needs the slot, which the second holds.
    r = env.post(h, first, "start")
    assert r.status_code == 409 and r.json()["error"]["code"] == "active_task_exists"


def test_idempotency_key_replays_and_detects_reuse(env):
    h = env.headers()
    first = env.create(h, 1, key="k1")
    assert first.status_code == 202
    again = env.create(h, 1, key="k1")
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
    assert env.create(h, 2, key="k1").status_code == 409
    assert env.create(h, 2, key="k1").json()["error"]["code"] == "idempotency_key_reuse"
    assert len(env.client.get(f"{API}/tasks", headers=h).json()["items"]) == 1


def test_daily_quota(env):
    env.settings.max_tasks_per_day = 2
    h = env.headers()
    for _ in range(2):
        tid = env.create(h).json()["id"]
        assert env.post(h, tid, "cancel").status_code == 200
    r = env.create(h)
    assert r.status_code == 429 and r.json()["error"]["code"] == "daily_limit_reached"
    env.clock.advance(days=1)
    assert env.create(h).status_code == 202


def test_task_list_is_newest_first_with_cursor(env):
    h = env.headers()
    ids = []
    for _ in range(3):
        tid = env.create(h).json()["id"]
        env.post(h, tid, "cancel")
        env.clock.advance(seconds=1)
        ids.append(tid)
    p1 = env.client.get(f"{API}/tasks?limit=2", headers=h).json()
    assert [t["id"] for t in p1["items"]] == ids[::-1][:2] and p1["nextCursor"]
    assert set(p1["items"][0]["issue"]) == {"number", "title", "url", "labels"}
    p2 = env.client.get(f"{API}/tasks?limit=2&cursor={p1['nextCursor']}", headers=h).json()
    assert [t["id"] for t in p2["items"]] == [ids[0]] and p2["nextCursor"] is None


def test_tasks_expire_after_retention(env):
    h = env.headers()
    tid = env.create(h).json()["id"]
    env.clock.advance(days=env.settings.task_retention_days, seconds=1)
    assert env.client.get(f"{API}/tasks/{tid}", headers=h).status_code == 404


# --- Human gates ----------------------------------------------------------------------------
def test_start_and_approve_require_the_right_state(env):
    h = env.headers()
    tid = env.create(h).json()["id"]
    assert env.post(h, tid, "start").status_code == 409  # still queued
    assert env.post(h, tid, "approve", {"diffHash": "sha256:" + "0" * 64}).status_code == 409
    assert env.post(h, tid, "reject").status_code == 409
    env.run_worker()
    assert env.post(h, tid, "approve", {"diffHash": "sha256:" + "0" * 64}).json()["error"]["code"] == "invalid_state"


def test_approval_must_match_the_stored_diff_hash(env):
    h = env.headers()
    task = drive_to_awaiting_approval(env, h)
    r = env.post(h, task["id"], "approve", {"diffHash": "sha256:" + "f" * 64})
    assert r.status_code == 409 and r.json()["error"]["code"] == "diff_hash_mismatch"
    assert env.get(h, task["id"])["status"] == "awaiting_approval"
    assert not env.run_worker()  # nothing dispatched, so no PR can be created


def test_reject_ends_the_task_without_a_pr(env):
    h = env.headers()
    task = drive_to_awaiting_approval(env, h)
    r = env.post(h, task["id"], "reject")
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert r.json()["approval"]["state"] == "rejected" and r.json()["pr"] is None
    assert not env.run_worker()
    assert env.post(h, task["id"], "approve", {"diffHash": task["finalDiffHash"]}).status_code == 409


def test_concurrent_approvals_create_exactly_one_pr(env):
    h = env.headers()
    task = drive_to_awaiting_approval(env, h)
    results: list[str] = []

    def approve():
        try:
            env.app.state.task_service.approve_task("1001", task["id"], task["finalDiffHash"])
            results.append("ok")
        except Exception as exc:
            results.append(getattr(exc, "code", "error"))

    threads = [threading.Thread(target=approve) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("ok") == 1 and results.count("invalid_state") == 5
    assert env.run_worker() and not env.run_worker()
    assert env.get(h, task["id"])["status"] == "completed"


# --- Cancellation ----------------------------------------------------------------------------
@pytest.mark.parametrize("state", ["queued", "awaiting_start"])
def test_cancel_before_work_is_immediate(env, state):
    h = env.headers()
    tid = env.create(h).json()["id"]
    if state == "awaiting_start":
        env.run_worker()
    r = env.post(h, tid, "cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert active_task(env, h) is None and not env.run_worker()


def test_cancel_while_worker_is_running_is_cooperative(env):
    h = env.headers()
    tid = env.create(h).json()["id"]
    job = env.worker_service.claim_next("w1")
    assert job is not None
    r = env.post(h, tid, "cancel")
    assert r.status_code == 202 and r.json()["cancelRequested"] is True
    assert r.json()["status"] == "planning"
    env.worker.process(job)  # first checkpoint observes the request
    task = env.get(h, tid)
    assert task["status"] == "cancelled" and active_task(env, h) is None


def test_awaiting_approval_and_creating_pr_cannot_be_cancelled(env):
    h = env.headers()
    task = drive_to_awaiting_approval(env, h)
    assert env.post(h, task["id"], "cancel").status_code == 409
    env.post(h, task["id"], "approve", {"diffHash": task["finalDiffHash"]})
    assert env.post(h, task["id"], "cancel").status_code == 409


# --- Failures ----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "scenario,status,code",
    [
        ("not_reproduced", "failed_not_reproduced", "not_reproduced"),
        ("no_fix", "failed_no_fix", "no_fix_found"),
    ],
)
def test_execution_failures_map_to_statuses(env, scenario, status, code):
    h = env.headers()
    tid = env.create(h).json()["id"]
    env.run_worker()
    env.post(h, tid, "start")
    env.agent.scenario = scenario
    env.run_worker()
    task = env.get(h, tid)
    assert task["status"] == status and task["failure"]["code"] == code
    assert task["failure"]["phase"] == "execute" and task["finishedAt"]
    assert active_task(env, h) is None and task["availableActions"] == ["run_again"]


def test_needs_input_and_planning_failure(env):
    h = env.headers()
    env.agent.scenario = "needs_input"
    tid = env.create(h).json()["id"]
    env.run_worker()
    task = env.get(h, tid)
    assert task["status"] == "needs_input" and task["plan"]["questions"]
    assert task["failure"]["code"] == "issue_ambiguous"

    env.agent.scenario = "plan_failed"
    tid = env.create(h, 2).json()["id"]
    env.run_worker()
    task = env.get(h, tid)
    assert task["status"] == "failed_infra" and task["failure"]["code"] == "llm_unavailable"


def test_unexpected_worker_exception_becomes_failed_infra_with_redacted_message(env):
    h = env.headers()

    class Boom:
        def plan(self, task, ctx):
            raise RuntimeError("boom api_key=supersecret123")

        def execute(self, task, ctx):
            raise AssertionError

    env.worker.agent = Boom()
    tid = env.create(h).json()["id"]
    env.run_worker()
    task = env.get(h, tid)
    assert task["status"] == "failed_infra" and task["failure"]["code"] == "internal_error"
    assert "supersecret123" not in task["failure"]["message"]


def test_lost_worker_is_reaped_and_slot_freed(env):
    h = env.headers()
    tid = env.create(h).json()["id"]
    job = env.worker_service.claim_next("w1")
    assert job and env.worker_service.reap_expired() == []  # lease still valid
    env.clock.advance(seconds=env.settings.lease_seconds + 1)
    assert env.worker_service.reap_expired() == [tid]
    task = env.get(h, tid)
    assert task["status"] == "worker_lost" and task["failure"]["phase"] == "plan"
    assert active_task(env, h) is None
    with pytest.raises(LeaseLost):
        env.worker_service.heartbeat(tid, "w1")
    assert env.worker_service.reap_expired() == []


def test_heartbeat_extends_the_lease(env):
    h = env.headers()
    tid = env.create(h).json()["id"]
    env.worker_service.claim_next("w1")
    env.clock.advance(seconds=60)
    assert env.worker_service.heartbeat(tid, "w1") is False
    env.clock.advance(seconds=60)  # 120s after claim, but renewed at 60s
    assert env.worker_service.reap_expired() == []


def test_a_second_worker_cannot_claim_or_write_a_leased_task(env):
    env.create(env.headers())
    job = env.worker_service.claim_next("w1")
    assert env.worker_service.claim_next("w2") is None
    with pytest.raises(LeaseLost):
        env.worker_service.append_event(job.task_id, "w2", EventSpec(EventType.INFO, "x"))


def test_pr_failure_can_be_retried_with_the_same_hash(env):
    h = env.headers()
    task = drive_to_awaiting_approval(env, h)

    class Failing:
        def create(self, task, diff):
            raise PullRequestError(FailureCode.GITHUB_ERROR, "GitHub returned 502")

    good = env.worker.pr_creator
    env.worker.pr_creator = Failing()
    env.post(h, task["id"], "approve", {"diffHash": task["finalDiffHash"]})
    env.run_worker()
    failed = env.get(h, task["id"])
    assert failed["status"] == "failed_infra" and failed["failure"]["phase"] == "pr"
    assert failed["pr"] is None and failed["availableActions"] == ["retry_pr", "run_again"]
    assert env.post(h, task["id"], "approve", {"diffHash": "sha256:" + "1" * 64}).status_code == 409

    env.worker.pr_creator = good
    assert env.post(h, task["id"], "approve", {"diffHash": task["finalDiffHash"]}).status_code == 200
    env.run_worker()
    done = env.get(h, task["id"])
    assert done["status"] == "completed" and done["failure"] is None and done["pr"]["url"]


# --- Events -----------------------------------------------------------------------------------
def test_event_messages_are_redacted_and_capped(env):
    h = env.headers()
    tid = env.create(h).json()["id"]
    job = env.worker_service.claim_next("w1")
    svc = env.worker_service
    svc.append_event(tid, "w1", EventSpec(EventType.TOOL_CALL, "used ghp_" + "a" * 30 + " " + "x" * 900))
    for _ in range(470):
        svc.append_event(job.task_id, "w1", EventSpec(EventType.INFO, "noise"))
    svc.append_event(tid, "w1", EventSpec(EventType.ERROR, "important", EventLevel.ERROR))
    events = env.client.get(f"{API}/tasks/{tid}/events?limit=50", headers=h).json()["items"]
    assert "ghp_" not in events[2]["message"] and len(events[2]["message"]) <= 500
    stored = env.store.get(f"tasks/{tid}")["eventSeq"]
    assert 450 < stored <= 500  # low-value events stop at 450; high-value ones still land
    last = env.client.get(f"{API}/tasks/{tid}/events?after={stored - 1}", headers=h).json()["items"]
    assert last[-1]["message"] == "important"


# --- Validation and errors ------------------------------------------------------------------------
def test_auth_and_validation_error_shapes(env):
    r = env.client.get(f"{API}/tasks")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    assert env.client.get(f"{API}/tasks", headers={"Authorization": "Bearer nope"}).status_code == 401
    h = env.headers()
    for body in (
        {"repoId": "bad", "issueNumber": 1},
        {"repoId": REPO_ID, "issueNumber": 0},
        {"repoId": REPO_ID, "issueNumber": 1, "extra": True},
        {"issueNumber": 1},
    ):
        r = env.client.post(f"{API}/tasks", json=body, headers=h)
        assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error", body
    assert env.client.get(f"{API}/tasks/not-an-id", headers=h).status_code == 400
    assert env.client.get(f"{API}/tasks?limit=51", headers=h).status_code == 400
    tid = env.create(h).json()["id"]
    assert env.client.get(f"{API}/tasks/{tid}/events?after=-1", headers=h).status_code == 400
    assert env.post(h, tid, "approve", {"diffHash": "md5:abc"}).status_code == 400


def test_oversized_body_is_rejected(env):
    r = env.client.post(
        f"{API}/tasks", headers=env.headers(),
        content=b"x" * (env.settings.max_body_bytes + 1)
    )
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"


def test_repository_and_github_errors(env):
    h = env.headers()
    assert env.create(h, repo="9_9").status_code == 404
    assert env.create(h, issue=999).status_code == 404
    assert env.client.get(f"{API}/repositories/9_9/issues", headers=h).status_code == 404
    repos = env.client.get(f"{API}/repositories", headers=h).json()["items"]
    assert repos[0]["id"] == REPO_ID and "installationId" not in repos[0]
    issues = env.client.get(f"{API}/repositories/{REPO_ID}/issues", headers=h).json()["items"]
    assert [i["number"] for i in issues] == [1, 2]
    env.github.unavailable = True
    r = env.create(h)
    assert r.status_code == 502 and r.json()["error"]["code"] == "github_unavailable"
    assert env.client.get(f"{API}/tasks", headers=h).status_code == 200  # reads do not need GitHub


def test_dev_token_is_disabled_outside_local_and_test(env):
    env.app.state.settings.environment = "production"
    r = env.client.post(f"{API}/auth/dev-token", json={"userId": "1", "githubLogin": "x"})
    assert r.status_code == 404


def test_health_needs_no_auth_and_openapi_lists_the_contract(env):
    assert env.client.get("/health").json() == {"status": "ok"}
    paths = env.client.get("/openapi.json").json()["paths"]
    for p in (f"{API}/tasks", f"{API}/tasks/{{task_id}}/start", f"{API}/tasks/{{task_id}}/approve"):
        assert p in paths
