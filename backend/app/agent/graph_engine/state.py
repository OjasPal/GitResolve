"""Typed RepairState, bounded reducers, and budget/checkpoint validation for Phase 2A."""
from __future__ import annotations

import os
import re
from typing import Annotated, Any, Literal, Mapping, NotRequired, TypedDict

from app.core.redaction import redact, redact_value

CHECKPOINT_SCHEMA_VERSION = "2a.v1"

DEFAULT_MAX_GRAPH_STEPS = 240
DEFAULT_MAX_TOOL_CALLS = 120
DEFAULT_MAX_PROVIDER_CALLS = 100
DEFAULT_MAX_INPUT_TOKENS = 300_000
DEFAULT_MAX_OUTPUT_TOKENS = 40_000
DEFAULT_MAX_STRATEGIES = 6

MAX_MESSAGES_IN_STATE = 30
MAX_ATTEMPTS_IN_STATE = 12
MAX_MESSAGE_CHARS = 1200


def get_engine_mode(override: str | None = None) -> Literal["graph", "legacy"]:
    raw = (override or os.environ.get("AGENT_ENGINE", "graph")).strip().lower()
    return "legacy" if raw == "legacy" else "graph"


def configured_budgets(task_limits: dict[str, Any] | None = None) -> dict[str, int]:
    limits = task_limits or {}
    return {
        "maxGraphSteps": int(os.environ.get("GITRESOLVE_MAX_GRAPH_STEPS", limits.get("maxGraphSteps", DEFAULT_MAX_GRAPH_STEPS))),
        "maxToolCalls": int(os.environ.get("GITRESOLVE_MAX_TOOL_CALLS", limits.get("maxToolCalls", DEFAULT_MAX_TOOL_CALLS))),
        "maxProviderCalls": int(os.environ.get("GITRESOLVE_MAX_PROVIDER_CALLS", limits.get("maxModelCalls", DEFAULT_MAX_PROVIDER_CALLS))),
        "maxInputTokens": int(os.environ.get("GITRESOLVE_MAX_INPUT_TOKENS", limits.get("maxInputTokens", DEFAULT_MAX_INPUT_TOKENS))),
        "maxOutputTokens": int(os.environ.get("GITRESOLVE_MAX_OUTPUT_TOKENS", limits.get("maxOutputTokens", DEFAULT_MAX_OUTPUT_TOKENS))),
        "maxStrategies": int(os.environ.get("GITRESOLVE_MAX_STRATEGIES", limits.get("maxRepairAttempts", DEFAULT_MAX_STRATEGIES))),
    }


def reduce_messages(
    existing: list[dict[str, Any]] | None,
    update: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Bounded message reducer with secret/host-path sanitization and compaction."""
    combined = list(existing or [])
    for item in update or []:
        if not isinstance(item, dict):
            continue
        sanitized = {
            "role": str(item.get("role", "tool"))[:32],
            "action": str(item.get("action", ""))[:64],
            "summary": redact(str(item.get("summary", "")), MAX_MESSAGE_CHARS),
        }
        if item.get("evidence_id"):
            sanitized["evidence_id"] = str(item["evidence_id"])[:64]
        if item.get("path"):
            sanitized["path"] = redact(str(item["path"]), 200)
        combined.append(sanitized)

    if len(combined) > MAX_MESSAGES_IN_STATE:
        retained_ids = [
            m["evidence_id"] for m in combined[:- (MAX_MESSAGES_IN_STATE - 1)]
            if m.get("evidence_id")
        ]
        compacted_header = {
            "role": "system",
            "action": "compacted_history",
            "summary": f"Compacted {len(combined) - (MAX_MESSAGES_IN_STATE - 1)} earlier steps.",
            "evidence_id": ",".join(retained_ids[:10]) if retained_ids else "compacted",
        }
        combined = [compacted_header, *combined[-(MAX_MESSAGES_IN_STATE - 1):]]
    return combined


def reduce_attempts(
    existing: list[dict[str, Any]] | None,
    update: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Bounded attempt reducer storing classified failure summaries and artifact references."""
    combined = list(existing or [])
    for item in update or []:
        if not isinstance(item, dict):
            continue
        clean = redact_value({
            "attempt": int(item.get("attempt", len(combined) + 1)),
            "category": str(item.get("category", "unknown"))[:64],
            "strategy": str(item.get("strategy", ""))[:200],
            "summary": redact(str(item.get("summary", "")), 600),
            "diff_hash": str(item.get("diff_hash", ""))[:80],
            "artifact_id": str(item.get("artifact_id", ""))[:80],
        })
        combined.append(clean)
    if len(combined) > MAX_ATTEMPTS_IN_STATE:
        combined = combined[-MAX_ATTEMPTS_IN_STATE:]
    return combined


class RepairState(TypedDict):
    """Durable, bounded LangGraph state for Phase 2A repair execution.

    Never stores raw repository file contents, full test logs, hidden reasoning,
    secrets, or host temporary paths.
    """
    task_id: str
    phase: str
    base_sha: str
    pinned_engine: Literal["graph", "legacy"]
    checkpoint_schema_version: str

    # Profile & environment metadata (references only, no file contents)
    profile_summary: dict[str, Any]
    declared_deps: list[str]
    entrypoint: str
    test_command: list[str]
    baseline_passed: bool
    baseline_category: str
    baseline_artifact_id: str

    # Adaptive Lanes state (shared with backend/app/agent/lanes/)
    lane: Literal["fast", "full"]
    risk_floor: Literal["low", "medium", "high"]
    triage_report: dict[str, Any]
    verification_plan: NotRequired[dict[str, Any] | None]
    oracle_evidence: NotRequired[dict[str, Any] | None]
    escalations: list[dict[str, Any]]

    # Investigation & localization state
    hypotheses: list[str]
    evidence_refs: list[str]
    localized_files: list[str]
    explored_files: list[str]

    # Rehydratable workspace overlay (compact validated edits only)
    overlay_edits: list[dict[str, Any]]
    overlay_tests: list[dict[str, str]]
    overlay_diff_hash: str
    proposal_explanation: str

    # Reducers
    messages: Annotated[list[dict[str, Any]], reduce_messages]
    attempt_records: Annotated[list[dict[str, Any]], reduce_attempts]

    # Loop & routing control
    current_attempt: int
    reproduced: bool
    static_check_passed: bool
    verification_passed: bool
    tests_run_count: int
    last_failure_category: str
    last_failure_summary: str
    action_history_hashes: list[str]
    repeated_action_count: int
    recovery_read_triggered: NotRequired[bool]

    # Budget counters & ceilings
    budgets: dict[str, int]
    budget_ceilings: dict[str, int]

    # Provider & review metadata
    provider_metadata: dict[str, Any]
    quality_review: NotRequired[dict[str, Any] | None]

    # Final ExecuteResult payload
    terminal_outcome: NotRequired[dict[str, Any] | None]


_FORBIDDEN_STATE_KEYS = frozenset({
    "originals",
    "raw_files",
    "full_logs",
    "hidden_reasoning",
    "thought_signature",
    "api_key",
    "private_key",
})

_HOST_TEMP_RE = re.compile(
    r"(?i)(?:[a-z]:[\\/]+Users[\\/]+|/(?:Users|home|tmp|var/folders)/[^\s'\"<>]+)"
)


def validate_checkpoint_payload(state: Mapping[str, Any] | dict[str, Any]) -> None:
    """Verify checkpoint state contains no forbidden keys, secrets, or host paths."""
    if not isinstance(state, dict):
        return
    if state.get("checkpoint_schema_version") and state["checkpoint_schema_version"] != CHECKPOINT_SCHEMA_VERSION:
        raise ValueError(f"Incompatible checkpoint schema version: {state.get('checkpoint_schema_version')}")
    forbidden = set(state.keys()) & _FORBIDDEN_STATE_KEYS
    if forbidden:
        raise ValueError(f"Checkpoint contains forbidden state keys: {sorted(forbidden)}")
    for key, val in state.items():
        if key == "overlay_tests":
            continue
        if isinstance(val, str) and len(val) > 16_000:
            raise ValueError(f"Checkpoint field {key} exceeds bounded size limit.")
        if isinstance(val, str) and _HOST_TEMP_RE.search(val):
            raise ValueError(f"Checkpoint field {key} contains an unredacted host path.")
