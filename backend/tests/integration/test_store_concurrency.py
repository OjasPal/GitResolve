"""Same lifecycle/concurrency checks against memory and an opt-in local emulator."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.errors import ApiError
from tests.conftest import build_env, REPO_ID


@pytest.mark.parametrize("backend", ["memory", "firestore"])
def test_concurrent_create_claim_and_approval(backend):
    env = build_env()
    if backend == "firestore":
        host = os.getenv("FIRESTORE_EMULATOR_HOST", "")
        if not host.startswith(("127.0.0.1:", "localhost:")):
            pytest.skip("A loopback Firestore emulator is required.")
        from app.services.store.firestore import FirestoreStore
        env.store = FirestoreStore(project=f"demo-gr-{uuid.uuid4().hex[:12]}")
        env.worker_service.store = env.store
        env.app.state.task_service.store = env.store
    user = str(uuid.uuid4().int)[:18]
    service = env.app.state.task_service
    ids = []
    try:
        def create(_):
            try:
                return service.create_task(user, REPO_ID, 1, "same-request")[0]["id"]
            except ApiError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(create, range(4)))
        assert len(set(results)) == 1 and results[0].startswith("tsk_")
        ids.append(results[0])
        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(env.worker_service.claim_next, ["w1"] * 4))
        jobs = [j for j in claims if j is not None]
        assert len(jobs) == 1
        env.worker.process(jobs[0])
        service.start_task(user, ids[0])
        env.worker.run_once()
        task = service.get_task(user, ids[0])
        def approve(_):
            try:
                service.approve_task(user, ids[0], task["finalDiffHash"])
                return "ok"
            except ApiError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=4) as pool:
            approvals = list(pool.map(approve, range(4)))
        assert approvals.count("ok") == 1
        env.worker.run_once()
        assert service.get_task(user, ids[0])["status"] == "completed"
        assert service.get_user(user)["activeTaskId"] is None
    finally:
        if backend == "firestore":
            for tid in ids:
                for sub in ("events", "artifacts"):
                    for doc in env.store._db.collection(f"tasks/{tid}/{sub}").stream():
                        doc.reference.delete()
                env.store._db.document(f"tasks/{tid}").delete()
            env.store._db.document(f"users/{user}").delete()
