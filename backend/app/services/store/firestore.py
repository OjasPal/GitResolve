"""Firestore implementation of the store (server SDK, Native mode).

Lifecycle and concurrency tests also run against an opt-in local emulator
(see backend/README.md). Production IAM/indexes need deployment validation.
"""

from collections.abc import Callable
from datetime import datetime
from typing import TypeVar
import random
import time

from google.api_core import exceptions as gexc
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.services.store.base import DELETE_FIELD, AlreadyExists, Doc, Store, Txn

T = TypeVar("T")


def _with_id(snapshot) -> Doc:
    data = snapshot.to_dict() or {}
    data.setdefault("id", snapshot.id)
    return data


def _convert(data: Doc) -> Doc:
    return {k: (firestore.DELETE_FIELD if v is DELETE_FIELD else v) for k, v in data.items()}


class _FsTxn(Txn):
    def __init__(self, db: firestore.Client, transaction: firestore.Transaction):
        self._db = db
        self._t = transaction

    def get(self, path: str) -> Doc | None:
        snap = self._db.document(path).get(transaction=self._t)
        return _with_id(snap) if snap.exists else None

    def create(self, path: str, data: Doc) -> None:
        self._t.create(self._db.document(path), data)

    def set(self, path: str, data: Doc) -> None:
        self._t.set(self._db.document(path), data)

    def update(self, path: str, data: Doc) -> None:
        self._t.update(self._db.document(path), _convert(data))


class FirestoreStore(Store):
    def __init__(self, project: str | None = None, database: str = "(default)"):
        self._db = firestore.Client(project=project, database=database)

    def run_transaction(self, fn: Callable[[Txn], T]) -> T:
        @firestore.transactional
        def _run(transaction: firestore.Transaction) -> T:
            return fn(_FsTxn(self._db, transaction))

        # Start fresh after contention with jitter. Immediate simultaneous retries
        # can repeatedly deadlock on the task/user pair (also in the emulator).
        # The callback is transactional and must have no external side effects.
        for attempt in range(4):
            try:
                return _run(self._db.transaction(max_attempts=1))
            except gexc.AlreadyExists as exc:
                raise AlreadyExists(str(exc)) from exc
            except (gexc.Aborted, ValueError) as exc:
                if not isinstance(exc, gexc.Aborted) and not isinstance(exc.__cause__, gexc.Aborted):
                    raise
                if attempt == 3:
                    raise
                time.sleep(random.uniform(0.05, 0.2) * (2 ** attempt))

    def get(self, path: str) -> Doc | None:
        snap = self._db.document(path).get()
        return _with_id(snap) if snap.exists else None

    def set(self, path: str, data: Doc) -> None:
        self._db.document(path).set(data)

    def list_user_tasks(self, user_id, limit, after_task_id):
        query = (
            self._db.collection("tasks")
            .where(filter=FieldFilter("userId", "==", user_id))
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
        )
        if after_task_id:
            anchor = self._db.collection("tasks").document(after_task_id).get()
            if not anchor.exists or anchor.to_dict().get("userId") != user_id:
                return []
            query = query.start_after(anchor)
        return [_with_id(s) for s in query.limit(limit).stream()]

    def count_user_tasks_since(self, user_id: str, since: datetime) -> int:
        query = (
            self._db.collection("tasks")
            .where(filter=FieldFilter("userId", "==", user_id))
            .where(filter=FieldFilter("createdAt", ">=", since))
        )
        return int(query.count().get()[0][0].value)

    def list_pending_dispatch(self, limit):
        query = self._db.collection("tasks").where(
            filter=FieldFilter("dispatchPending", "==", True)
        )
        return [_with_id(s) for s in query.limit(limit).stream()]

    def list_expired_leases(self, now, limit):
        query = self._db.collection("tasks").where(
            filter=FieldFilter("leaseExpiresAt", "<=", now)
        )
        return [_with_id(s) for s in query.limit(limit).stream()]

    def list_events(self, task_id, after_seq, limit):
        query = (
            self._db.collection(f"tasks/{task_id}/events")
            .where(filter=FieldFilter("seq", ">", after_seq))
            .order_by("seq")
            .limit(limit)
        )
        return [_with_id(s) for s in query.stream()]

    def list_artifacts(self, task_id):
        return [_with_id(s) for s in self._db.collection(f"tasks/{task_id}/artifacts").stream()]
