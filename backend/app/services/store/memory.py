"""In-memory store: local development and tests. Thread-safe, not durable."""

import copy
import threading
from collections.abc import Callable
from datetime import datetime
from typing import TypeVar

from app.services.store.base import DELETE_FIELD, AlreadyExists, Doc, Store, Txn

T = TypeVar("T")


class _MemTxn(Txn):
    def __init__(self, docs: dict[str, Doc]):
        self._docs = docs
        self._writes: list[tuple[str, str, Doc]] = []

    def _view(self, path: str) -> Doc | None:
        doc = self._docs.get(path)
        return copy.deepcopy(doc) if doc is not None else None

    def get(self, path: str) -> Doc | None:
        return self._view(path)

    def create(self, path: str, data: Doc) -> None:
        self._writes.append(("create", path, copy.deepcopy(data)))

    def set(self, path: str, data: Doc) -> None:
        self._writes.append(("set", path, copy.deepcopy(data)))

    def update(self, path: str, data: Doc) -> None:
        self._writes.append(("update", path, copy.deepcopy(data)))

    def commit(self) -> None:
        # Validate first so a failing write leaves nothing applied.
        staged = dict(self._docs)
        for op, path, data in self._writes:
            if op == "create":
                if path in staged:
                    raise AlreadyExists(path)
                staged[path] = data
            elif op == "set":
                staged[path] = data
            else:
                if path not in staged:
                    raise KeyError(f"update of missing document: {path}")
                merged = dict(staged[path])
                for key, value in data.items():
                    if value is DELETE_FIELD:
                        merged.pop(key, None)
                    else:
                        merged[key] = value
                staged[path] = merged
        self._docs.clear()
        self._docs.update(staged)


class MemoryStore(Store):
    def __init__(self) -> None:
        self._docs: dict[str, Doc] = {}
        self._lock = threading.RLock()

    def run_transaction(self, fn: Callable[[Txn], T]) -> T:
        with self._lock:
            txn = _MemTxn(self._docs)
            result = fn(txn)
            txn.commit()
            return result

    def get(self, path: str) -> Doc | None:
        with self._lock:
            doc = self._docs.get(path)
            return copy.deepcopy(doc) if doc is not None else None

    def set(self, path: str, data: Doc) -> None:
        with self._lock:
            self._docs[path] = copy.deepcopy(data)

    def _tasks(self) -> list[Doc]:
        return [
            copy.deepcopy(d)
            for p, d in self._docs.items()
            if p.startswith("tasks/") and p.count("/") == 1
        ]

    def list_user_tasks(self, user_id, limit, after_task_id):
        with self._lock:
            rows = sorted(
                (t for t in self._tasks() if t.get("userId") == user_id),
                key=lambda t: (t["createdAt"], t["id"]),
                reverse=True,
            )
            if after_task_id:
                ids = [t["id"] for t in rows]
                rows = rows[ids.index(after_task_id) + 1 :] if after_task_id in ids else []
            return rows[:limit]

    def count_user_tasks_since(self, user_id, since):
        with self._lock:
            return sum(
                1
                for t in self._tasks()
                if t.get("userId") == user_id and t["createdAt"] >= since
            )

    def list_pending_dispatch(self, limit):
        with self._lock:
            rows = [t for t in self._tasks() if t.get("dispatchPending") is True]
            rows.sort(key=lambda t: t["createdAt"])
            return rows[:limit]

    def list_expired_leases(self, now, limit):
        with self._lock:
            rows = [
                t
                for t in self._tasks()
                if t.get("leaseExpiresAt") is not None and t["leaseExpiresAt"] <= now
            ]
            return rows[:limit]

    def _sub(self, task_id: str, name: str) -> list[Doc]:
        prefix = f"tasks/{task_id}/{name}/"
        return [
            copy.deepcopy(d)
            for p, d in sorted(self._docs.items())
            if p.startswith(prefix) and p.count("/") == 3
        ]

    def list_events(self, task_id, after_seq, limit):
        with self._lock:
            rows = [e for e in self._sub(task_id, "events") if e["seq"] > after_seq]
            rows.sort(key=lambda e: e["seq"])
            return rows[:limit]

    def list_artifacts(self, task_id):
        with self._lock:
            return self._sub(task_id, "artifacts")
