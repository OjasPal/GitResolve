"""Store interface. Only this package touches Firestore (Backend Schema, section 1).

Business logic is written once in `services/task_service.py` against `Txn`;
`MemoryStore` (local/tests) and `FirestoreStore` (deployed) implement it.
Documents are plain dicts with camelCase keys and timezone-aware datetimes.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime
from typing import Any, TypeVar

Doc = dict[str, Any]
T = TypeVar("T")


class _DeleteField:
    """Sentinel for `Txn.update`: remove the field (maps to DELETE_FIELD)."""

    def __repr__(self) -> str:
        return "DELETE_FIELD"

    # Singleton: identity checks must survive copy.deepcopy in store backends.
    def __copy__(self) -> "_DeleteField":
        return self

    def __deepcopy__(self, memo: dict) -> "_DeleteField":
        return self


DELETE_FIELD = _DeleteField()


class AlreadyExists(Exception):
    """Raised by `Txn.create` when the path already holds a document."""


class Txn(ABC):
    """Transaction handle. Reads must precede writes (Firestore rule)."""

    @abstractmethod
    def get(self, path: str) -> Doc | None: ...

    @abstractmethod
    def create(self, path: str, data: Doc) -> None:
        """Create; AlreadyExists is raised at commit if the path exists."""

    @abstractmethod
    def set(self, path: str, data: Doc) -> None: ...

    @abstractmethod
    def update(self, path: str, data: Doc) -> None: ...


class Store(ABC):
    @abstractmethod
    def run_transaction(self, fn: Callable[[Txn], T]) -> T:
        """Run `fn` atomically; retried on contention. `fn` must be idempotent."""

    # --- Non-transactional reads / queries ---------------------------------
    @abstractmethod
    def get(self, path: str) -> Doc | None: ...

    @abstractmethod
    def set(self, path: str, data: Doc) -> None: ...

    @abstractmethod
    def list_user_tasks(
        self, user_id: str, limit: int, after_task_id: str | None
    ) -> list[Doc]:
        """Newest first. Requires index tasks(userId ASC, createdAt DESC)."""

    @abstractmethod
    def count_user_tasks_since(self, user_id: str, since: datetime) -> int: ...

    @abstractmethod
    def list_pending_dispatch(self, limit: int) -> list[Doc]:
        """Tasks with dispatchPending == True (automatic single-field index)."""

    @abstractmethod
    def list_expired_leases(self, now: datetime, limit: int) -> list[Doc]:
        """Tasks with leaseExpiresAt < now; unleased tasks omit the field."""

    @abstractmethod
    def list_events(self, task_id: str, after_seq: int, limit: int) -> list[Doc]: ...

    @abstractmethod
    def list_artifacts(self, task_id: str) -> list[Doc]: ...
