"""SQLite and Store/Firestore LangGraph checkpointers with schema, engine, lane-policy pinning, and lease fencing."""
from __future__ import annotations

import base64
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import closing
from pathlib import Path
import os
import pickle
import sqlite3
import tempfile
import threading
import time
from typing import Any
import zlib

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph_engine.state import (
    CHECKPOINT_SCHEMA_VERSION,
    LANES_POLICY_VERSION,
    MAX_CHECKPOINT_BYTES,
    validate_checkpoint_payload,
)


class CheckpointCompatibilityError(RuntimeError):
    """Raised when a checkpoint violates engine pinning, schema version, or base SHA invariants."""


def _current_adaptive_lanes_mode() -> str:
    val = os.environ.get("ADAPTIVE_LANES", "on").strip().lower()
    return "on" if val in {"1", "true", "on", "yes"} else "off"


def default_checkpoint_db_path() -> Path:
    configured = os.environ.get("GITRESOLVE_SQLITE_CHECKPOINT_PATH", "").strip()
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / "gitresolve-checkpoints-2a.sqlite"


def make_thread_id(task_id: str, phase: str = "execute") -> str:
    return f"{task_id}:{phase}"


def parse_thread_id(thread_id: str) -> tuple[str, str]:
    if ":" in thread_id:
        task_id, phase = thread_id.split(":", 1)
        return task_id, phase or "execute"
    return thread_id, "execute"


class SqliteRepairCheckpointer(InMemorySaver):
    """Durable local SQLite checkpointer for LangGraph repair execution.

    Synchronizes concurrent LangGraph BackgroundExecutor calls with an RLock
    and persists serialized storage, writes, and blobs alongside pinned engine
    mode, checkpoint schema version, base commit SHA, and Adaptive Lanes policy pin.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        fence_check: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._lock = threading.RLock()
        self._fence_check = fence_check
        self.db_path = Path(db_path) if db_path is not None else default_checkpoint_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._thread_pins: dict[str, dict[str, Any]] = {}
        self._loaded_threads: set[str] = set()
        self._init_db()

    def _enforce_fence(self) -> None:
        if self._fence_check is not None:
            self._fence_check()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS repair_checkpoints (
                    thread_id TEXT PRIMARY KEY,
                    pinned_engine TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    base_sha TEXT NOT NULL,
                    lanes_policy_version TEXT NOT NULL DEFAULT 'al.v1',
                    adaptive_lanes_mode TEXT NOT NULL DEFAULT 'on',
                    lane_policy_mismatch INTEGER NOT NULL DEFAULT 0,
                    lane_policy_mismatch_reason TEXT NOT NULL DEFAULT '',
                    storage_blob BLOB NOT NULL,
                    writes_blob BLOB NOT NULL,
                    blobs_blob BLOB NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            for col_sql in (
                "ALTER TABLE repair_checkpoints ADD COLUMN lanes_policy_version TEXT NOT NULL DEFAULT 'al.v1'",
                "ALTER TABLE repair_checkpoints ADD COLUMN adaptive_lanes_mode TEXT NOT NULL DEFAULT 'on'",
                "ALTER TABLE repair_checkpoints ADD COLUMN lane_policy_mismatch INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE repair_checkpoints ADD COLUMN lane_policy_mismatch_reason TEXT NOT NULL DEFAULT ''",
            ):
                try:
                    conn.execute(col_sql)
                except sqlite3.OperationalError:
                    pass
            conn.commit()

    def has_lane_policy_mismatch(self, thread_id: str) -> tuple[bool, str]:
        with self._lock:
            self._load_thread(thread_id)
            pin = self._thread_pins.get(thread_id) or {}
            return bool(pin.get("lane_policy_mismatch", False)), str(pin.get("lane_policy_mismatch_reason", ""))

    def pin_or_verify_thread(
        self,
        thread_id: str,
        *,
        engine_mode: str,
        base_sha: str,
        schema_version: str = CHECKPOINT_SCHEMA_VERSION,
        lanes_policy_version: str = LANES_POLICY_VERSION,
        adaptive_lanes_mode: str | None = None,
        check_fence: bool = True,
    ) -> dict[str, Any]:
        """Verify an existing thread's pinned engine/schema/base_sha/lane-policy or register a new pin."""
        with self._lock:
            if check_fence:
                self._enforce_fence()
            current_al_mode = adaptive_lanes_mode if adaptive_lanes_mode is not None else _current_adaptive_lanes_mode()
            self._load_thread(thread_id)
            existing = self._thread_pins.get(thread_id)
            if existing is not None:
                if existing.get("schema_version") != schema_version:
                    raise CheckpointCompatibilityError(
                        f"Incompatible checkpoint schema version for {thread_id}: "
                        f"stored={existing.get('schema_version')}, expected={schema_version}"
                    )
                if existing.get("pinned_engine") != engine_mode:
                    raise CheckpointCompatibilityError(
                        f"Pinned engine mismatch for {thread_id}: "
                        f"stored={existing.get('pinned_engine')}, requested={engine_mode}"
                    )
                if base_sha and existing.get("base_sha") and existing.get("base_sha") != base_sha:
                    raise CheckpointCompatibilityError(
                        f"Base commit SHA mismatch for {thread_id}: "
                        f"stored={existing.get('base_sha')}, requested={base_sha}"
                    )
                changed = False
                if base_sha and not existing.get("base_sha"):
                    existing["base_sha"] = base_sha
                    changed = True

                stored_policy = existing.get("lanes_policy_version") or LANES_POLICY_VERSION
                stored_al_mode = existing.get("adaptive_lanes_mode") or current_al_mode
                if stored_policy != lanes_policy_version or stored_al_mode != current_al_mode:
                    reason = (
                        f"Lane policy pin changed across resume "
                        f"(stored policy={stored_policy}/mode={stored_al_mode}, "
                        f"current policy={lanes_policy_version}/mode={current_al_mode})"
                    )
                    existing["lane_policy_mismatch"] = True
                    existing["lane_policy_mismatch_reason"] = reason
                    # Never downgrade: pin to CURRENT policy and force adaptive_lanes_mode='off' (full lane)
                    existing["lanes_policy_version"] = lanes_policy_version
                    existing["adaptive_lanes_mode"] = "off"
                    changed = True

                if changed:
                    self._persist_thread(thread_id)
                return dict(existing)

            pin: dict[str, Any] = {
                "pinned_engine": engine_mode,
                "schema_version": schema_version,
                "base_sha": base_sha,
                "lanes_policy_version": lanes_policy_version,
                "adaptive_lanes_mode": current_al_mode,
                "lane_policy_mismatch": False,
                "lane_policy_mismatch_reason": "",
            }
            self._thread_pins[thread_id] = pin
            self._persist_thread(thread_id)
            return dict(pin)

    def _compact_thread_buffers(self, thread_id: str) -> tuple[dict[str, dict[str, Any]], dict[Any, Any], dict[Any, Any]]:
        """Prune historical checkpoints to the most recent 2 per namespace so storage stays compact O(1)."""
        ns_map: dict[str, dict[str, Any]] = {}
        kept_cp_ids: set[str] = set()
        kept_channel_versions: set[tuple[str, str, Any]] = set()

        for ns, cp_map in self.storage.get(thread_id, {}).items():
            if not cp_map:
                continue
            sorted_ids = sorted(cp_map.keys())[-2:]
            pruned_cp_map = {cid: cp_map[cid] for cid in sorted_ids}
            ns_map[ns] = pruned_cp_map
            kept_cp_ids.update(sorted_ids)
            for cid in sorted_ids:
                cp_tuple = pruned_cp_map[cid]
                try:
                    loaded_cp = self.serde.loads_typed(cp_tuple[0])
                    for chan, ver in (loaded_cp.get("channel_versions") or {}).items():
                        kept_channel_versions.add((ns, chan, ver))
                except Exception:
                    pass

        thread_writes = {
            k: dict(v)
            for k, v in self.writes.items()
            if k[0] == thread_id and (not kept_cp_ids or k[2] in kept_cp_ids)
        }
        thread_blobs = {
            k: v
            for k, v in self.blobs.items()
            if k[0] == thread_id and (not kept_channel_versions or (k[1], k[2], k[3]) in kept_channel_versions)
        }
        return ns_map, thread_writes, thread_blobs

    def _load_thread(self, thread_id: str) -> bool:
        return self._load_thread_from_sqlite(thread_id)

    def _persist_thread(self, thread_id: str) -> None:
        self._persist_thread_to_sqlite(thread_id)

    def _load_thread_from_sqlite(self, thread_id: str) -> bool:
        with self._lock:
            if thread_id in self._loaded_threads:
                return thread_id in self._thread_pins
            self._loaded_threads.add(thread_id)
            with closing(self._connect()) as conn:
                row = conn.execute(
                    "SELECT * FROM repair_checkpoints WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()
            if row is None:
                return False

            row_keys = set(row.keys())
            self._thread_pins[thread_id] = {
                "pinned_engine": str(row["pinned_engine"]),
                "schema_version": str(row["schema_version"]),
                "base_sha": str(row["base_sha"]),
                "lanes_policy_version": str(row["lanes_policy_version"]) if "lanes_policy_version" in row_keys else LANES_POLICY_VERSION,
                "adaptive_lanes_mode": str(row["adaptive_lanes_mode"]) if "adaptive_lanes_mode" in row_keys else "on",
                "lane_policy_mismatch": bool(row["lane_policy_mismatch"]) if "lane_policy_mismatch" in row_keys else False,
                "lane_policy_mismatch_reason": str(row["lane_policy_mismatch_reason"]) if "lane_policy_mismatch_reason" in row_keys else "",
            }
            stored_ns_map: dict[str, dict[str, Any]] = pickle.loads(row["storage_blob"])
            ns_default: defaultdict[str, dict[str, Any]] = defaultdict(dict)
            for ns, cp_map in stored_ns_map.items():
                ns_default[ns] = dict(cp_map)
            self.storage[thread_id] = ns_default

            stored_writes: dict[tuple[str, str, str], dict[tuple[str, int], Any]] = pickle.loads(
                row["writes_blob"]
            )
            for k, v in stored_writes.items():
                self.writes[k] = dict(v)

            stored_blobs: dict[tuple[str, str, str, str | int | float], tuple[str, bytes]] = pickle.loads(
                row["blobs_blob"]
            )
            for k, v in stored_blobs.items():
                self.blobs[k] = v
            return True

    def _persist_thread_to_sqlite(self, thread_id: str) -> None:
        with self._lock:
            pin = self._thread_pins.get(thread_id, {
                "pinned_engine": "graph",
                "schema_version": CHECKPOINT_SCHEMA_VERSION,
                "base_sha": "",
                "lanes_policy_version": LANES_POLICY_VERSION,
                "adaptive_lanes_mode": _current_adaptive_lanes_mode(),
                "lane_policy_mismatch": False,
                "lane_policy_mismatch_reason": "",
            })
            ns_map, thread_writes, thread_blobs = self._compact_thread_buffers(thread_id)
            storage_bytes = pickle.dumps(ns_map, protocol=pickle.HIGHEST_PROTOCOL)
            writes_bytes = pickle.dumps(thread_writes, protocol=pickle.HIGHEST_PROTOCOL)
            blobs_bytes = pickle.dumps(thread_blobs, protocol=pickle.HIGHEST_PROTOCOL)
            total_size = len(storage_bytes) + len(writes_bytes) + len(blobs_bytes)
            if total_size > MAX_CHECKPOINT_BYTES:
                raise ValueError(
                    f"Serialized checkpoint for {thread_id} ({total_size} bytes) exceeds MAX_CHECKPOINT_BYTES ({MAX_CHECKPOINT_BYTES})."
                )
            with closing(self._connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO repair_checkpoints (
                        thread_id, pinned_engine, schema_version, base_sha,
                        lanes_policy_version, adaptive_lanes_mode,
                        lane_policy_mismatch, lane_policy_mismatch_reason,
                        storage_blob, writes_blob, blobs_blob, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(thread_id) DO UPDATE SET
                        pinned_engine = excluded.pinned_engine,
                        schema_version = excluded.schema_version,
                        base_sha = excluded.base_sha,
                        lanes_policy_version = excluded.lanes_policy_version,
                        adaptive_lanes_mode = excluded.adaptive_lanes_mode,
                        lane_policy_mismatch = excluded.lane_policy_mismatch,
                        lane_policy_mismatch_reason = excluded.lane_policy_mismatch_reason,
                        storage_blob = excluded.storage_blob,
                        writes_blob = excluded.writes_blob,
                        blobs_blob = excluded.blobs_blob,
                        updated_at = excluded.updated_at
                    """,
                    (
                        thread_id,
                        pin["pinned_engine"],
                        pin["schema_version"],
                        pin["base_sha"],
                        pin.get("lanes_policy_version", LANES_POLICY_VERSION),
                        pin.get("adaptive_lanes_mode", "on"),
                        1 if pin.get("lane_policy_mismatch") else 0,
                        pin.get("lane_policy_mismatch_reason", ""),
                        storage_bytes,
                        writes_bytes,
                        blobs_bytes,
                        time.time(),
                    ),
                )
                conn.commit()

    def _verify_config_pin(self, config: RunnableConfig) -> None:
        with self._lock:
            thread_id = config["configurable"]["thread_id"]
            self._load_thread(thread_id)
            pin = self._thread_pins.get(thread_id)
            expected_engine = config["configurable"].get("pinned_engine")
            expected_schema = config["configurable"].get("schema_version", CHECKPOINT_SCHEMA_VERSION)
            if pin is not None:
                if pin.get("schema_version") != expected_schema:
                    raise CheckpointCompatibilityError(
                        f"Checkpoint schema mismatch for {thread_id}: "
                        f"stored={pin.get('schema_version')}, expected={expected_schema}"
                    )
                if expected_engine and pin.get("pinned_engine") != expected_engine:
                    raise CheckpointCompatibilityError(
                        f"Checkpoint pinned engine mismatch for {thread_id}: "
                        f"stored={pin.get('pinned_engine')}, requested={expected_engine}"
                    )

    def get_delta_channel_history(
        self, *, config: RunnableConfig, channels: Sequence[str]
    ) -> Mapping[str, Any]:
        with self._lock:
            self._verify_config_pin(config)
            return super().get_delta_channel_history(config=config, channels=channels)

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        with self._lock:
            self._verify_config_pin(config)
            tup = super().get_tuple(config)
            if tup is not None:
                channel_values = tup.checkpoint.get("channel_values", {})
                if isinstance(channel_values, dict):
                    validate_checkpoint_payload(channel_values)
            return tup

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        with self._lock:
            if config and "configurable" in config and "thread_id" in config["configurable"]:
                self._verify_config_pin(config)
            items = list(super().list(config, filter=filter, before=before, limit=limit))
        yield from items

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        with self._lock:
            self._enforce_fence()
            thread_id = config["configurable"]["thread_id"]
            channel_values = checkpoint.get("channel_values", {})
            if isinstance(channel_values, dict):
                validate_checkpoint_payload(channel_values)
                engine_mode = str(channel_values.get("pinned_engine") or config["configurable"].get("pinned_engine") or "graph")
                schema_version = str(channel_values.get("checkpoint_schema_version") or CHECKPOINT_SCHEMA_VERSION)
                base_sha = str(channel_values.get("base_sha") or "")
                existing_pin = self._thread_pins.get(thread_id)
                lanes_policy = str(
                    channel_values.get("lanes_policy_version")
                    or (existing_pin or {}).get("lanes_policy_version")
                    or LANES_POLICY_VERSION
                )
                al_mode = str(
                    (existing_pin or {}).get("adaptive_lanes_mode")
                    or channel_values.get("pinned_adaptive_lanes")
                    or _current_adaptive_lanes_mode()
                )
                self.pin_or_verify_thread(
                    thread_id,
                    engine_mode=engine_mode,
                    base_sha=base_sha,
                    schema_version=schema_version,
                    lanes_policy_version=lanes_policy,
                    adaptive_lanes_mode=al_mode,
                    check_fence=False,
                )
            updated_config = super().put(config, checkpoint, metadata, new_versions)
            self._persist_thread(thread_id)
            return updated_config

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        with self._lock:
            self._enforce_fence()
            thread_id = config["configurable"]["thread_id"]
            for chan, val in writes:
                if isinstance(val, dict) and chan in {"__root__", ""}:
                    validate_checkpoint_payload(val)
            super().put_writes(config, writes, task_id, task_path)
            self._persist_thread(thread_id)

    def delete_thread(self, thread_id: str) -> None:
        with self._lock:
            super().delete_thread(thread_id)
            self._thread_pins.pop(thread_id, None)
            self._loaded_threads.discard(thread_id)
            with closing(self._connect()) as conn:
                conn.execute("DELETE FROM repair_checkpoints WHERE thread_id = ?", (thread_id,))
                conn.commit()


class StoreRepairCheckpointer(SqliteRepairCheckpointer):
    """Compact Store/Firestore-backed LangGraph checkpointer for Phase 2B.

    Stores compressed, size-bounded checkpoints at `tasks/{task_id}/checkpoints/{phase}`
    using the existing `Store` (`MemoryStore` / `FirestoreStore`) without any external
    paid database, and enforces transactional lease fencing (`worker_id` must own the
    active task lease) on every write.
    """

    def __init__(
        self,
        store: Any,
        *,
        task_id: str | None = None,
        worker_id: str | None = None,
        clock: Callable[[], Any] | None = None,
        fence_check: Callable[[], None] | None = None,
    ) -> None:
        self.store = store
        self.bound_task_id = task_id
        self.worker_id = worker_id
        self.clock = clock
        super().__init__(db_path=default_checkpoint_db_path(), fence_check=fence_check)

    def _init_db(self) -> None:
        # Store-backed saver does not need a local SQLite table
        pass

    @staticmethod
    def doc_path(thread_id: str) -> str:
        t_id, phase = parse_thread_id(thread_id)
        return f"tasks/{t_id}/checkpoints/{phase}"

    @staticmethod
    def _encode_blob(data: Any) -> str:
        raw = pickle.dumps(data, protocol=pickle.HIGHEST_PROTOCOL)
        compressed = zlib.compress(raw, level=6)
        return base64.b64encode(compressed).decode("ascii")

    @staticmethod
    def _decode_blob(encoded: str) -> Any:
        if not encoded:
            return {}
        compressed = base64.b64decode(encoded.encode("ascii"))
        raw = zlib.decompress(compressed)
        return pickle.loads(raw)

    def _verify_store_lease_in_txn(self, txn: Any, task_id: str) -> None:
        """Transactional fencing check: reject stale workers whose lease expired or changed owner."""
        if not self.worker_id:
            return
        task_doc = txn.get(f"tasks/{task_id}")
        if not isinstance(task_doc, Mapping):
            return
        from app.services.worker_service import LeaseLost
        owner = task_doc.get("leaseOwner")
        if owner != self.worker_id:
            raise LeaseLost(
                f"Worker {self.worker_id} no longer owns lease for {task_id} (current owner={owner})."
            )
        expires_at = task_doc.get("leaseExpiresAt")
        if expires_at is not None and self.clock is not None:
            now = self.clock()
            if expires_at <= now:
                raise LeaseLost(f"Worker {self.worker_id} lease for {task_id} has expired.")
        if task_doc.get("cancelRequested"):
            raise LeaseLost(f"Task {task_id} was cancelled.")

    def _load_thread(self, thread_id: str) -> bool:
        with self._lock:
            if thread_id in self._loaded_threads:
                return thread_id in self._thread_pins
            self._loaded_threads.add(thread_id)
            doc = self.store.get(self.doc_path(thread_id))
            if not isinstance(doc, Mapping) or not doc.get("pinnedEngine"):
                return False

            self._thread_pins[thread_id] = {
                "pinned_engine": str(doc["pinnedEngine"]),
                "schema_version": str(doc["schemaVersion"]),
                "base_sha": str(doc.get("baseSha", "")),
                "lanes_policy_version": str(doc.get("lanesPolicyVersion") or LANES_POLICY_VERSION),
                "adaptive_lanes_mode": str(doc.get("adaptiveLanesMode") or "on"),
                "lane_policy_mismatch": bool(doc.get("lanePolicyMismatch", False)),
                "lane_policy_mismatch_reason": str(doc.get("lanePolicyMismatchReason", "")),
            }
            stored_ns_map: dict[str, dict[str, Any]] = self._decode_blob(str(doc.get("storageB64", "")))
            ns_default: defaultdict[str, dict[str, Any]] = defaultdict(dict)
            for ns, cp_map in stored_ns_map.items():
                ns_default[ns] = dict(cp_map)
            self.storage[thread_id] = ns_default

            stored_writes: dict[tuple[str, str, str], dict[tuple[str, int], Any]] = self._decode_blob(
                str(doc.get("writesB64", ""))
            )
            for k, v in stored_writes.items():
                self.writes[k] = dict(v)

            stored_blobs: dict[tuple[str, str, str, str | int | float], tuple[str, bytes]] = self._decode_blob(
                str(doc.get("blobsB64", ""))
            )
            for k, v in stored_blobs.items():
                self.blobs[k] = v
            return True

    def _persist_thread(self, thread_id: str) -> None:
        with self._lock:
            task_id, phase = parse_thread_id(thread_id)
            pin = self._thread_pins.get(thread_id, {
                "pinned_engine": "graph",
                "schema_version": CHECKPOINT_SCHEMA_VERSION,
                "base_sha": "",
                "lanes_policy_version": LANES_POLICY_VERSION,
                "adaptive_lanes_mode": _current_adaptive_lanes_mode(),
                "lane_policy_mismatch": False,
                "lane_policy_mismatch_reason": "",
            })
            ns_map, thread_writes, thread_blobs = self._compact_thread_buffers(thread_id)
            has_cp = any(bool(cp_map) for cp_map in ns_map.values())
            storage_b64 = self._encode_blob(ns_map)
            writes_b64 = self._encode_blob(thread_writes)
            blobs_b64 = self._encode_blob(thread_blobs)
            byte_size = len(storage_b64) + len(writes_b64) + len(blobs_b64)
            if byte_size > MAX_CHECKPOINT_BYTES:
                raise ValueError(
                    f"Store checkpoint for {thread_id} ({byte_size} bytes) exceeds MAX_CHECKPOINT_BYTES ({MAX_CHECKPOINT_BYTES})."
                )
            doc_payload = {
                "threadId": thread_id,
                "taskId": task_id,
                "phase": phase,
                "pinnedEngine": pin["pinned_engine"],
                "schemaVersion": pin["schema_version"],
                "baseSha": pin["base_sha"],
                "lanesPolicyVersion": pin.get("lanes_policy_version", LANES_POLICY_VERSION),
                "adaptiveLanesMode": pin.get("adaptive_lanes_mode", "on"),
                "lanePolicyMismatch": bool(pin.get("lane_policy_mismatch", False)),
                "lanePolicyMismatchReason": str(pin.get("lane_policy_mismatch_reason", "")),
                "hasCheckpoint": has_cp,
                "storageB64": storage_b64,
                "writesB64": writes_b64,
                "blobsB64": blobs_b64,
                "byteSize": byte_size,
                "updatedAt": time.time(),
            }
            path = self.doc_path(thread_id)
            if hasattr(self.store, "run_transaction"):
                def _write_txn(txn: Any) -> None:
                    self._verify_store_lease_in_txn(txn, task_id)
                    txn.set(path, doc_payload)
                self.store.run_transaction(_write_txn)
            elif hasattr(self.store, "set"):
                self.store.set(path, doc_payload)

    def delete_thread(self, thread_id: str) -> None:
        with self._lock:
            super(SqliteRepairCheckpointer, self).delete_thread(thread_id)
            self._thread_pins.pop(thread_id, None)
            self._loaded_threads.discard(thread_id)
            path = self.doc_path(thread_id)
            task_id, _ = parse_thread_id(thread_id)
            if hasattr(self.store, "run_transaction"):
                def _del_txn(txn: Any) -> None:
                    self._verify_store_lease_in_txn(txn, task_id)
                    txn.set(path, {"threadId": thread_id, "hasCheckpoint": False, "deleted": True})
                self.store.run_transaction(_del_txn)
            elif hasattr(self.store, "set"):
                self.store.set(path, {"threadId": thread_id, "hasCheckpoint": False, "deleted": True})


FirestoreRepairCheckpointer = StoreRepairCheckpointer

__all__ = [
    "CheckpointCompatibilityError",
    "FirestoreRepairCheckpointer",
    "SqliteRepairCheckpointer",
    "StoreRepairCheckpointer",
    "default_checkpoint_db_path",
    "make_thread_id",
    "parse_thread_id",
]
