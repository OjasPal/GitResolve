"""Monotonic fast->full lane escalation and budget enforcement."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import time
from typing import Any

from app.agent.lanes.types import (
     EscalationDecision,
    EscalationReason,
    FAST_MAX_PROVIDER_CALLS,
    FAST_SUB_DEADLINE_SECONDS,
    LaneName,
    OracleEvidence,
    RISK_ORDER,
    RiskFloor,
    TriageReport,
    VerificationPlan,
    max_risk,
)


@dataclass(frozen=True)
class LaneRuntimeState:
    current_lane: LaneName
    risk_floor: RiskFloor
    triage: TriageReport
    plan: VerificationPlan | None = None
    oracle: OracleEvidence | None = None
    escalations: tuple[EscalationDecision, ...] = ()
    fast_calls_used: int = 0
    fast_input_tokens: int = 0
    fast_output_tokens: int = 0
    fast_started_at: float = field(default_factory=time.monotonic)
    fast_sub_deadline_seconds: float = FAST_SUB_DEADLINE_SECONDS
    carried_evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def escalated(self) -> bool:
        return len(self.escalations) > 0 or self.current_lane == LaneName.FULL


def record_fast_usage(
    state: LaneRuntimeState,
    calls: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> LaneRuntimeState:
    if calls < 0 or input_tokens < 0 or output_tokens < 0:
        raise ValueError("Fast-lane usage counters must be non-negative.")
    return replace(
        state,
        fast_calls_used=state.fast_calls_used + calls,
        fast_input_tokens=state.fast_input_tokens + input_tokens,
        fast_output_tokens=state.fast_output_tokens + output_tokens,
    )


def check_fast_budget(
    state: LaneRuntimeState,
    additional_calls: int = 0,
    now_monotonic: float | None = None,
    task_remaining_seconds: float | None = None,
) -> tuple[bool, EscalationReason | None, str]:
    now = time.monotonic() if now_monotonic is None else now_monotonic
    elapsed = max(0.0, now - state.fast_started_at)
    effective_deadline = state.fast_sub_deadline_seconds
    if task_remaining_seconds is not None:
        effective_deadline = min(effective_deadline, max(0.0, task_remaining_seconds))
    if elapsed > effective_deadline:
        return (
            False,
            EscalationReason.FAST_BUDGET_EXHAUSTED,
            f"Fast-lane sub-deadline exceeded ({elapsed:.2f}s > {effective_deadline:.2f}s).",
        )
    if state.fast_calls_used + additional_calls > FAST_MAX_PROVIDER_CALLS:
        return (
            False,
            EscalationReason.FAST_BUDGET_EXHAUSTED,
            f"Fast-lane provider call budget exceeded ({state.fast_calls_used + additional_calls} > {FAST_MAX_PROVIDER_CALLS}).",
        )
    return True, None, ""


def escalate(
    state: LaneRuntimeState,
    reason: EscalationReason,
    detail: str,
    *,
    target_lane: LaneName = LaneName.FULL,
    new_risk_floor: RiskFloor | None = None,
    evidence: dict[str, Any] | None = None,
    now_monotonic: float | None = None,
) -> tuple[LaneRuntimeState, EscalationDecision]:
    """Monotonically escalate from fast to full, preserving evidence and used budgets."""
    if state.current_lane == LaneName.FULL and target_lane == LaneName.FAST:
        raise ValueError("Lane transition is strictly monotonic: cannot downgrade from full to fast.")
    if target_lane != LaneName.FULL:
        raise ValueError("Escalation target must be the full lane.")
    if new_risk_floor is not None and RISK_ORDER[new_risk_floor] < RISK_ORDER[state.risk_floor]:
        raise ValueError("Risk floor is strictly monotonic: cannot lower risk floor.")

    effective_floor = max_risk(state.risk_floor, new_risk_floor or RiskFloor.MEDIUM)
    now = time.monotonic() if now_monotonic is None else now_monotonic
    elapsed = max(0.0, now - state.fast_started_at)

    merged_evidence = dict(state.carried_evidence)
    merged_evidence["triage"] = state.triage.to_dict()
    if state.plan is not None:
        merged_evidence["verificationPlan"] = state.plan.to_dict()
    if state.oracle is not None:
        merged_evidence["oracle"] = state.oracle.to_dict()
    if evidence:
        merged_evidence.update(evidence)

    decision = EscalationDecision(
        from_lane=state.current_lane,
        to_lane=LaneName.FULL,
        reason=reason,
        detail=detail,
        risk_floor=effective_floor,
        preserved_evidence=merged_evidence,
        used_model_calls=state.fast_calls_used,
        used_input_tokens=state.fast_input_tokens,
        used_output_tokens=state.fast_output_tokens,
        elapsed_seconds=elapsed,
    )

    updated = replace(
        state,
        current_lane=LaneName.FULL,
        risk_floor=effective_floor,
        escalations=state.escalations + (decision,),
        carried_evidence=merged_evidence,
    )
    return updated, decision
