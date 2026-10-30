"""Runs only when FIRESTORE_EMULATOR_HOST points to a local emulator."""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("google.cloud.firestore")

from app.services.store.base import AlreadyExists, DELETE_FIELD
from app.services.store.firestore import FirestoreStore

EMULATOR_HOST = os.getenv("FIRESTORE_EMULATOR_HOST")
pytestmark = pytest.mark.skipif(
    not EMULATOR_HOST,
    reason="Set FIRESTORE_EMULATOR_HOST to run Firestore emulator integration tests.",
)


def test_firestore_store_queries_and_transactions_against_emulator():
    store = FirestoreStore(project="demo-gitresolve")
    suffix = uuid.uuid4().hex
    user_id = f"emulator-{suffix}"
    task_ids = [f"tsk_{suffix[:10]}{n:010x}" for n in range(2)]
    now = datetime.now(timezone.utc)
    task_collection = store._db.collection("tasks")
    try:
        store.set(
            f"users/{user_id}",
            {"githubUserId": 999999, "activeTaskId": task_ids[0], "removeMe": True},
        )
        for index, task_id in enumerate(task_ids):
            store.set(
                f"tasks/{task_id}",
                {
                    "id": task_id,
                    "userId": user_id,
                    "createdAt": now + timedelta(seconds=index),
                    "dispatchPending": index == 0,
                    "leaseExpiresAt": now - timedelta(seconds=1) if index == 0 else None,
                },
            )
        store.set(
            f"tasks/{task_ids[0]}/events/000001",
            {"seq": 1, "message": "created"},
        )
        store.set(
            f"tasks/{task_ids[0]}/artifacts/plan",
            {"id": "plan", "content": "plan"},
        )

        def update_user(txn):
            user = txn.get(f"users/{user_id}")
            assert user is not None
            txn.update(f"users/{user_id}", {"activeTaskId": task_ids[1], "removeMe": DELETE_FIELD})
            return user["githubUserId"]

        assert store.run_transaction(update_user) == 999999
        assert store.get(f"users/{user_id}") == {
            "githubUserId": 999999,
            "activeTaskId": task_ids[1],
            "id": user_id,
        }
        with pytest.raises(AlreadyExists):
            store.run_transaction(lambda txn: txn.create(f"users/{user_id}", {"bad": True}))

        assert store.count_user_tasks_since(user_id, now - timedelta(seconds=1)) == 2
        page1 = store.list_user_tasks(user_id, 1, None)
        assert page1[0]["id"] == task_ids[1]
        page2 = store.list_user_tasks(user_id, 1, task_ids[1])
        assert page2[0]["id"] == task_ids[0]
        pending = {task["id"] for task in store.list_pending_dispatch(100)}
        expired = {task["id"] for task in store.list_expired_leases(now, 100)}
        assert task_ids[0] in pending
        assert task_ids[0] in expired
        assert [event["seq"] for event in store.list_events(task_ids[0], 0, 10)] == [1]
        assert [artifact["id"] for artifact in store.list_artifacts(task_ids[0])] == ["plan"]
    finally:
        for task_id in task_ids:
            for collection in ("events", "artifacts"):
                for snapshot in store._db.collection(f"tasks/{task_id}/{collection}").stream():
                    snapshot.reference.delete()
            task_collection.document(task_id).delete()
        store._db.document(f"users/{user_id}").delete()
