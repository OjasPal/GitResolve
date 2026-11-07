"""Typed RepairState, bounded reducers, and budget/checkpoint validation for Phase 2A/2B/3."""
from __future__ import annotations

import json
import os
import re
from typing import Annotated, Any, Literal, Mapping, NotRequired, TypedDict

from app.agent.lanes.types import LANES_POLICY_VERSION
from app.core.redaction import redact, redact_value

CHECKPOINT_SCHEMA_VERSION = "2a.v1"
MAX_CHECKPOINT_BYTES = 240_000
MAX_PRESERVED_EVIDENCE_BYTES = 4_000
MAX_SPECIALIST_CALLS_PER_TASK = 2

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
        "maxSpecialistCalls": int(os.environ.get("GITRESOLVE_MAX_SPECIALIST_CALLS", limits.get("maxSpecialistCalls", MAX_SPECIALIST_CALLS_PER_TASK))),
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
            m["evidence_id"] for m in combined[: -(MAX_MESSAGES_IN_STATE - 1)]
            if m.get("evidence_id")
        ]
        compacted_header = {
            "role": "system",
            "action": "compacted_history",
            "summary": f"Compacted {len(combined) - (MAX_MESSAGES_IN_STATE - 1)} earlier steps.",
            "evidence_id": ",".join(retained_ids[:10]) if retained_ids else "compacted",
        }
        combined = [compacted_header, *combined[-(MAX_MESSAGES_IN_STATE - 1) :]]
    return combined


def reduce_attempts(
    existing: list[dict[str, Any]] | None,
    update: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Bounded attempt reducer storing classified failure summaries, evidence, and required strategy changes."""
    combined = list(existing or [])
    for item in update or []:
        if not isinstance(item, dict):
            continue
        clean = redact_value({
            "attempt": int(item.get("attempt", len(combined) + 1)),
            "fingerprint": str(item.get("fingerprint", ""))[:80],
            "category": str(item.get("category", "unknown_internal"))[:64],
            "strategy": str(item.get("strategy", ""))[:200],
            "summary": redact(str(item.get("summary", "")), 600),
            "what_was_tried": redact(str(item.get("what_was_tried", item.get("strategy", ""))), 240),
            "evidence": redact(str(item.get("evidence", item.get("summary", ""))), 300),
            "why_it_failed": redact(str(item.get("why_it_failed", item.get("summary", ""))), 260),
            "rejected_hypothesis": redact(str(item.get("rejected_hypothesis", item.get("strategy", ""))), 240),
            "required_change": redact(str(item.get("required_change", "")), 300),
            "diff_hash": str(item.get("diff_hash", ""))[:80],
            "artifact_id": str(item.get("artifact_id", ""))[:80],
        })
        combined.append(clean)
    if len(combined) > MAX_ATTEMPTS_IN_STATE:
        combined = combined[-MAX_ATTEMPTS_IN_STATE:]
    return combined


def reduce_candidates(
    existing: list[dict[str, Any]] | None,
    update: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Bounded reducer for best-of-N parallel candidate branches via LangGraph Send."""
    if update is not None and any(isinstance(x, dict) and x.get("__clear__") for x in update):
        return [item for item in update if isinstance(item, dict) and not item.get("__clear__")][-2:]
    combined = list(existing or [])
    for item in update or []:
        if isinstance(item, dict) and not item.get("__clear__"):
            combined.append(item)
    return combined[-2:]


class RepairState(TypedDict):
    """Durable, bounded LangGraph state for Phase 2A/2B/3 repair execution.

    Never stores raw repository file contents, full test logs, hidden reasoning,
    secrets, environmental circuit breaker state, or host temporary paths.
    """
    task_id: str
    phase: str
    base_sha: str
    pinned_engine: Literal["graph", "legacy"]
    checkpoint_schema_version: str
    lanes_policy_version: NotRequired[str]
    pinned_adaptive_lanes: NotRequired[Literal["on", "off"]]
    crash_resumes: NotRequired[int]

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
    fast_usage: NotRequired[dict[str, Any]]

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
    candidate_branches: NotRequired[Annotated[list[dict[str, Any]], reduce_candidates]]

    # Loop, strategy router & specialist control
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
    patch_loop_done: NotRequired[bool]
    patch_turns_in_attempt: NotRequired[int]
    specialist_calls_used: NotRequired[int]
    specialist_advice: NotRequired[str]
    strategy_action: NotRequired[str]
    flaky_reruns_used: NotRequired[int]
    env_prep_retries: NotRequired[int]
    fast_anchor_retries: NotRequired[int]

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
    "provider_health",
    "circuit_breaker",
    "breaker_state",
})

_HOST_TEMP_RE = re.compile(
    r"(?i)(?:[a-z]:[\\/]+Users[\\/]+|/(?:Users|home|tmp|var/folders)/[^\s'\"<>]+)"
)


def validate_checkpoint_payload(state: Mapping[str, Any] | dict[str, Any]) -> None:
    """Verify checkpoint state contains no forbidden keys, secrets, host paths, or oversized blobs."""
    if not isinstance(state, Mapping):
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

    escalations = state.get("escalations")
    if isinstance(escalations, list):
        if len(escalations) > 16:
            raise ValueError("Checkpoint escalations list exceeds bounded length (16).")
        for idx, esc in enumerate(escalations):
            if isinstance(esc, Mapping):
                pe = esc.get("preservedEvidence") or esc.get("preserved_evidence") or {}
                if isinstance(pe, Mapping):
                    if any(k in pe for k in ("triage", "verificationPlan", "oracle", "fastCandidateDiff")):
                        raise ValueError(
                            f"Escalation[{idx}].preservedEvidence embeds full uncompacted evidence; store compact refs/hashes only."
                        )
                    pe_bytes = len(json.dumps(pe, default=str).encode("utf-8"))
                    if pe_bytes > MAX_PRESERVED_EVIDENCE_BYTES:
                        raise ValueError(
                            f"Escalation[{idx}].preservedEvidence exceeds {MAX_PRESERVED_EVIDENCE_BYTES} bytes ({pe_bytes})."
                        )

    total_bytes = len(json.dumps(dict(state), default=str).encode("utf-8"))
    if total_bytes > MAX_CHECKPOINT_BYTES:
        raise ValueError(
            f"Checkpoint payload size ({total_bytes} bytes) exceeds MAX_CHECKPOINT_BYTES ({MAX_CHECKPOINT_BYTES})."
        )


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "LANES_POLICY_VERSION",
    "MAX_CHECKPOINT_BYTES",
    "MAX_PRESERVED_EVIDENCE_BYTES",
    "MAX_SPECIALIST_CALLS_PER_TASK",
    "RepairState",
    "configured_budgets",
    "get_engine_mode",
    "reduce_attempts",
    "reduce_candidates",
    "reduce_messages",
    "validate_checkpoint_payload",
]
