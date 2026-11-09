"""Single-process local store, with optional atomic persistence for development."""

import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from app.core.errors import PersistenceError, public_error
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
        if self._writes:
            raise PersistenceError("ReadAfterWriteError: Attempted read after write in a transaction.")
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
    def __init__(self, state_path: Path | None = None) -> None:
        self._docs: dict[str, Doc] = {}
        self._lock = threading.RLock()
        self._state_path = Path(state_path) if state_path else None
        self._owner = None
        self._owner_pid = os.getpid()
        if self._state_path:
            self._state_path = self._state_path.resolve()
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._owner = open(str(self._state_path) + ".lock", "a+b")
            try:
                if os.name == "nt":
                    import msvcrt
                    self._owner.seek(0)
                    if not self._owner.read(1):
                        self._owner.write(b"0")
                        self._owner.flush()
                    self._owner.seek(0)
                    msvcrt.locking(self._owner.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self._owner.close()
                self._owner = None
                raise PersistenceError("Local state is already owned. Use the embedded worker or Firestore for separate processes.") from None
        try:
            if self._state_path and self._state_path.exists():
                self._docs = json.loads(self._state_path.read_text(encoding="utf-8"),
                                        object_hook=lambda v: datetime.fromisoformat(v["__datetime__"])
                                        if set(v) == {"__datetime__"} else v)
                if not isinstance(self._docs, dict):
                    raise ValueError("Local state file must contain a document dictionary.")
        except Exception:
            self.close()
            raise

    def close(self):
        if self._owner is not None:
            self._owner.close()
            self._owner = None

    def __del__(self):
        self.close()

    def _persist(self, docs) -> None:
        if self._state_path is None:
            return
        if self._owner is None or self._owner_pid != os.getpid():
            raise PersistenceError("Local state ownership is no longer valid. Reopen the store in a single process.")
        def encode(value):
            if isinstance(value, datetime):
                return {"__datetime__": value.isoformat()}
            raise TypeError(type(value).__name__)
        temporary = None
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self._state_path.parent,
                                             prefix=".gitresolve-state-", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(docs, stream, default=encode)
                stream.flush()
                os.fsync(stream.fileno())
            for attempt in range(5):
                try:
                    os.replace(temporary, self._state_path)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.025 * 2 ** attempt)
        except (OSError, TypeError, ValueError) as exc:
            raise PersistenceError(public_error(exc).message) from exc
        finally:
            if temporary:
                for attempt in range(5):
                    try:
                        temporary.unlink(missing_ok=True)
                        break
                    except PermissionError as exc:
                        if attempt == 4:
                            raise PersistenceError(public_error(exc).message) from exc
                        time.sleep(0.025 * 2 ** attempt)

    def run_transaction(self, fn: Callable[[Txn], T]) -> T:
        with self._lock:
            docs = copy.deepcopy(self._docs) if self._state_path else self._docs
            txn = _MemTxn(docs)
            result = fn(txn)
            txn.commit()
            if docs != self._docs:
                self._persist(docs)
            self._docs = docs
            return result

    def get(self, path: str) -> Doc | None:
        with self._lock:
            doc = self._docs.get(path)
            return copy.deepcopy(doc) if doc is not None else None

    def set(self, path: str, data: Doc) -> None:
        with self._lock:
            docs = copy.deepcopy(self._docs) if self._state_path else self._docs
            docs[path] = copy.deepcopy(data)
            if docs != self._docs:
                self._persist(docs)
            self._docs = docs

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
