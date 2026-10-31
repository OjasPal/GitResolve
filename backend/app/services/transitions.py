"""Transactional building blocks shared by the API and worker services.

Every status change goes through `apply_change`, which validates the
transition, maintains the user's active-task slot, appends events and writes
the task document inside one transaction (Backend Schema, section 5).
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.core.config import Settings
from app.core.errors import ApiError, invalid_state, not_found
from app.core.redaction import redact
from app.schemas.enums import EventLevel, EventType, TaskStage, TaskStatus
from app.services.ids import event_id
from app.services.lifecycle import (
    TERMINAL_STATUSES,
    can_transition,
    is_active,
)
from app.services.store.base import DELETE_FIELD, Doc, Txn

EVENT_HARD_CAP = 500
EVENT_TRIM_AFTER = 450
EVENT_MESSAGE_MAX = 500
EVENT_DATA_MAX_BYTES = 2048
HIGH_VALUE_EVENTS = frozenset(
    {
        EventType.STATUS_CHANGE,
        EventType.ERROR,
        EventType.POLICY_CHECK,
        EventType.ATTEMPT,
        EventType.APPROVAL,
        EventType.PR_CREATED,
        EventType.CANCELLATION,
    }
)
LEASE_FIELDS = ("leaseOwner", "leaseExpiresAt", "heartbeatAt")


class LeaseLost(Exception):
    """The caller no longer owns the task lease; it must stop work."""


def task_path(task_id: str) -> str:
    return f"tasks/{task_id}"


def user_path(user_id: str) -> str:
    return f"users/{user_id}"


def event_path(task_id: str, seq: int) -> str:
    return f"tasks/{task_id}/events/{event_id(seq)}"


def artifact_path(task_id: str, artifact_id: str) -> str:
    return f"tasks/{task_id}/artifacts/{artifact_id}"


@dataclass
class EventSpec:
    type: EventType
    message: str
    level: EventLevel = EventLevel.INFO
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Ctx:
    task: Doc
    user: Doc | None
    holder_active: bool  # user's slot is held by a *different* active task


def release_lease() -> dict[str, Any]:
    return {name: DELETE_FIELD for name in LEASE_FIELDS}


def read_ctx(txn: Txn, task_id: str, *, owner: str | None = None, now: datetime | None = None) -> Ctx:
    """Read task + user (+ slot holder). Missing, expired or foreign -> 404."""
    task = txn.get(task_path(task_id))
    if task is None:
        raise not_found()
    if owner is not None and task["userId"] != owner:
        raise not_found()
    if now is not None and task.get("expiresAt") is not None and task["expiresAt"] <= now:
        raise not_found()
    user = txn.get(user_path(task["userId"]))
    holder_active = False
    holder_id = user.get("activeTaskId") if user else None
    if holder_id and holder_id != task_id:
        holder = txn.get(task_path(holder_id))
        holder_active = holder is not None and is_active(holder["status"])
    return Ctx(task=task, user=user, holder_active=holder_active)


def active_slot_error(holder_id: str | None) -> ApiError:
    return ApiError(
        409,
        "active_task_exists",
        "You already have an active task.",
        {"activeTaskId": holder_id} if holder_id else None,
    )


def build_event(task: Doc, seq: int, stage: str, spec: EventSpec, now: datetime, settings: Settings) -> Doc:
    # Metadata is a closed, scalar-only vocabulary, never arbitrary tool output.
    allowed = {"from", "to", "stage", "code", "n", "number", "diffHash",
               "durationMs", "ran", "passed", "failed", "timedOut", "attempt",
               "modelCalls", "inputTokens", "outputTokens", "execSeconds"}
    data = {
        key: redact(value, 200) if isinstance(value, str) else value
        for key, value in spec.data.items()
        if key in allowed and isinstance(value, (str, int, float, bool, type(None)))
    }
    if len(json.dumps(data, default=str).encode()) > EVENT_DATA_MAX_BYTES:
        data = {"truncated": True}
    return {
        "seq": seq,
        "ts": now,
        "stage": stage,
        "type": spec.type.value,
        "level": spec.level.value,
        "message": redact(spec.message, EVENT_MESSAGE_MAX),
        "data": data,
        "expiresAt": task["createdAt"] + timedelta(days=settings.subdoc_retention_days),
    }


def apply_change(
    txn: Txn,
    ctx: Ctx,
    settings: Settings,
    now: datetime,
    *,
    to_status: TaskStatus | None = None,
    stage: TaskStage | None = None,
    updates: dict[str, Any] | None = None,
    events: list[EventSpec] | None = None,
) -> Doc:
    """Validate and write one change; returns the task document after the write."""
    task = ctx.task
    upd: dict[str, Any] = dict(updates or {})
    pending_events = list(events or [])
    user_update: dict[str, Any] | None = None
    current = TaskStatus(task["status"])

    if to_status is not None and to_status != current:
        if not can_transition(current, to_status):
            raise invalid_state(f"Cannot move task from {current.value} to {to_status.value}.")
        was_active, will_be_active = is_active(current), is_active(to_status)
        if will_be_active and not was_active:
            if ctx.holder_active:
                raise active_slot_error(ctx.user.get("activeTaskId") if ctx.user else None)
            user_update = {"activeTaskId": task["id"]}
        elif was_active and not will_be_active:
            if ctx.user and ctx.user.get("activeTaskId") == task["id"]:
                user_update = {"activeTaskId": None}
        upd["status"] = to_status.value
        upd["statusUpdatedAt"] = now
        if to_status in TERMINAL_STATUSES:
            upd.setdefault("finishedAt", now)
            upd["dispatchPending"] = False
        pending_events.insert(
            0,
            EventSpec(
                EventType.STATUS_CHANGE,
                f"Status changed to {to_status.value}.",
                data={"from": current.value, "to": to_status.value},
            ),
        )

    if stage is not None and stage.value != task["stage"]:
        upd["stage"] = stage.value
        pending_events.append(
            EventSpec(EventType.STAGE_CHANGE, f"Stage: {stage.value}.", data={"stage": stage.value})
        )

    current_stage = upd.get("stage", task["stage"])
    seq = task.get("eventSeq", 0)
    for spec in pending_events:
        if seq >= EVENT_HARD_CAP:
            break
        if seq >= EVENT_TRIM_AFTER and spec.type not in HIGH_VALUE_EVENTS:
            continue
        seq += 1
        txn.create(event_path(task["id"], seq), build_event(task, seq, current_stage, spec, now, settings))
    upd["eventSeq"] = seq
    upd["updatedAt"] = now

    txn.update(task_path(task["id"]), upd)
    if user_update is not None:
        txn.update(user_path(task["userId"]), user_update)

    merged = {**task}
    for key, value in upd.items():
        if value is DELETE_FIELD:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged
