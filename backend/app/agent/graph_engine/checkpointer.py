"""SQLite-backed LangGraph checkpointer with schema and engine pinning for Phase 2A."""
from __future__ import annotations

import os
import pickle
import sqlite3
import tempfile
import threading
import time
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

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
    validate_checkpoint_payload,
)


class CheckpointCompatibilityError(RuntimeError):
    """Raised when a checkpoint violates engine pinning, schema version, or base SHA invariants."""


def default_checkpoint_db_path() -> Path:
    configured = os.environ.get("GITRESOLVE_SQLITE_CHECKPOINT_PATH", "").strip()
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / "gitresolve-checkpoints-2a.sqlite"


def make_thread_id(task_id: str, phase: str = "execute") -> str:
    return f"{task_id}:{phase}"


class SqliteRepairCheckpointer(InMemorySaver):
    """Durable local SQLite checkpointer for Phase 2A LangGraph repair execution.

    Synchronizes concurrent LangGraph BackgroundExecutor calls with an RLock
    and persists serialized storage, writes, and blobs alongside pinned engine
    mode, checkpoint schema version, and base commit SHA.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        super().__init__()
        self._lock = threading.RLock()
        self.db_path = Path(db_path) if db_path is not None else default_checkpoint_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._thread_pins: dict[str, dict[str, str]] = {}
        self._loaded_threads: set[str] = set()
        self._init_db()

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
                    storage_blob BLOB NOT NULL,
                    writes_blob BLOB NOT NULL,
                    blobs_blob BLOB NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.commit()

    def pin_or_verify_thread(
        self,
        thread_id: str,
        *,
        engine_mode: str,
        base_sha: str,
        schema_version: str = CHECKPOINT_SCHEMA_VERSION,
    ) -> dict[str, str]:
        """Verify an existing thread's pinned engine/schema/base_sha or register a new pin."""
        with self._lock:
            self._load_thread_from_sqlite(thread_id)
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
                if base_sha and not existing.get("base_sha"):
                    existing["base_sha"] = base_sha
                    self._persist_thread_to_sqlite(thread_id)
                return dict(existing)

            pin = {
                "pinned_engine": engine_mode,
                "schema_version": schema_version,
                "base_sha": base_sha,
            }
            self._thread_pins[thread_id] = pin
            self._persist_thread_to_sqlite(thread_id)
            return dict(pin)

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

            self._thread_pins[thread_id] = {
                "pinned_engine": str(row["pinned_engine"]),
                "schema_version": str(row["schema_version"]),
                "base_sha": str(row["base_sha"]),
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
            })
            ns_map = {ns: dict(cp_map) for ns, cp_map in self.storage.get(thread_id, {}).items()}
            thread_writes = {
                k: dict(v) for k, v in self.writes.items() if k[0] == thread_id
            }
            thread_blobs = {
                k: v for k, v in self.blobs.items() if k[0] == thread_id
            }
            with closing(self._connect()) as conn:
                conn.execute(
                    """
                    INSERT INTO repair_checkpoints (
                        thread_id, pinned_engine, schema_version, base_sha,
                        storage_blob, writes_blob, blobs_blob, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(thread_id) DO UPDATE SET
                        pinned_engine = excluded.pinned_engine,
                        schema_version = excluded.schema_version,
                        base_sha = excluded.base_sha,
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
                        pickle.dumps(ns_map, protocol=pickle.HIGHEST_PROTOCOL),
                        pickle.dumps(thread_writes, protocol=pickle.HIGHEST_PROTOCOL),
                        pickle.dumps(thread_blobs, protocol=pickle.HIGHEST_PROTOCOL),
                        time.time(),
                    ),
                )
                conn.commit()

    def _verify_config_pin(self, config: RunnableConfig) -> None:
        with self._lock:
            thread_id = config["configurable"]["thread_id"]
            self._load_thread_from_sqlite(thread_id)
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
            thread_id = config["configurable"]["thread_id"]
            channel_values = checkpoint.get("channel_values", {})
            if isinstance(channel_values, dict):
                validate_checkpoint_payload(channel_values)
                engine_mode = str(channel_values.get("pinned_engine") or config["configurable"].get("pinned_engine") or "graph")
                schema_version = str(channel_values.get("checkpoint_schema_version") or CHECKPOINT_SCHEMA_VERSION)
                base_sha = str(channel_values.get("base_sha") or "")
                self.pin_or_verify_thread(
                    thread_id,
                    engine_mode=engine_mode,
                    base_sha=base_sha,
                    schema_version=schema_version,
                )
            updated_config = super().put(config, checkpoint, metadata, new_versions)
            self._persist_thread_to_sqlite(thread_id)
            return updated_config

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        with self._lock:
            thread_id = config["configurable"]["thread_id"]
            for chan, val in writes:
                if isinstance(val, dict) and chan in {"__root__", ""}:
                    validate_checkpoint_payload(val)
            super().put_writes(config, writes, task_id, task_path)
            self._persist_thread_to_sqlite(thread_id)

    def delete_thread(self, thread_id: str) -> None:
        with self._lock:
            super().delete_thread(thread_id)
            self._thread_pins.pop(thread_id, None)
            self._loaded_threads.discard(thread_id)
            with closing(self._connect()) as conn:
                conn.execute("DELETE FROM repair_checkpoints WHERE thread_id = ?", (thread_id,))
                conn.commit()
