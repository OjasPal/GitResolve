"""Deterministic and random identifier helpers."""

import hashlib
import secrets


def task_id_for(user_id: str, idempotency_key: str | None) -> str:
    """`tsk_` + 20 hex chars; derived from user + key when a key is supplied."""
    if idempotency_key:
        digest = hashlib.sha256(f"{user_id}:{idempotency_key}".encode()).hexdigest()
        return f"tsk_{digest[:20]}"
    return f"tsk_{secrets.token_hex(10)}"


def event_id(seq: int) -> str:
    return f"{seq:06d}"


def attempt_id(n: int) -> str:
    return f"{n:03d}"


def sha256_hash(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode()).hexdigest()
