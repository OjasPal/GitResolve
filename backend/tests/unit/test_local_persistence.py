from datetime import datetime, timezone

import pytest

from app.services.store.memory import MemoryStore


def test_local_state_survives_restart_with_datetimes(tmp_path):
    path = tmp_path / "store.json"
    store = MemoryStore(path)
    now = datetime.now(timezone.utc)
    store.run_transaction(lambda tx: tx.create("tasks/test", {"createdAt": now, "status": "awaiting_approval"}))
    store.close()
    restored = MemoryStore(path)
    assert restored.get("tasks/test") == {"createdAt": now, "status": "awaiting_approval"}


def test_failed_write_does_not_change_memory_or_disk(tmp_path, monkeypatch):
    path = tmp_path / "store.json"
    store = MemoryStore(path)
    store.set("users/test", {"login": "original"})
    def fail_replace(*args):
        raise OSError("disk failure")
    monkeypatch.setattr("app.services.store.memory.os.replace", fail_replace)
    with pytest.raises(OSError):
        store.run_transaction(lambda tx: tx.update("users/test", {"login": "changed"}))
    assert store.get("users/test")["login"] == "original"
    store.close()
    assert MemoryStore(path).get("users/test")["login"] == "original"


def test_corrupt_state_is_not_silently_discarded(tmp_path):
    path = tmp_path / "store.json"
    path.write_text("invalid JSON")
    with pytest.raises(ValueError):
        MemoryStore(path)
