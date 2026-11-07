"""Monotonic fast->full lane escalation and budget enforcement."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
import hashlib
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


def _compact_evidence_summary(
    triage: TriageReport,
    plan: VerificationPlan | None,
    oracle: OracleEvidence | None,
    extra: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build compact references/hashes for EscalationDecision.preserved_evidence without embedding full copies."""
    compact: dict[str, Any] = {
        "triageRef": {
            "candidateLane": triage.candidate_lane.value,
            "riskFloor": triage.risk_floor.value,
            "taskType": triage.task_type_floor.value,
            "localizedFiles": list(triage.localized_files[:5]),
        }
    }
    if plan is not None:
        compact["planRef"] = {
            "finalLane": plan.final_lane.value,
            "verificationLevel": plan.verification_level.value,
            "changeClass": plan.patch_facts.change_class.value,
        }
    if oracle is not None:
        compact["oracleRef"] = {
            "level": oracle.level.value,
            "diffHash": oracle.diff_hash,
            "validForFastLane": oracle.valid_for_fast_lane,
            "executionIds": list(oracle.execution_ids[:4]),
        }
    if extra:
        for k, v in extra.items():
            if k in {"triage", "verificationPlan", "oracle"}:
                continue
            if k == "fastCandidateDiff" and isinstance(v, str):
                compact["fastCandidateDiffHash"] = "sha256:" + hashlib.sha256(v.encode("utf-8")).hexdigest()
                compact["fastCandidateDiffLines"] = len(v.splitlines())
            elif k == "fastReview" and isinstance(v, Mapping):
                compact["fastReviewApproved"] = bool(v.get("approved"))
                compact["fastReviewBlockingCount"] = len(v.get("blocking_issues") or [])
            elif isinstance(v, (int, float, bool)):
                compact[k] = v
            elif isinstance(v, str):
                compact[k] = v[:240]
    return compact


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
    fast_started_at: float = field(default_factory=time.time)
    fast_elapsed_seconds: float = 0.0
    fast_sub_deadline_seconds: float = FAST_SUB_DEADLINE_SECONDS
    carried_evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def escalated(self) -> bool:
        return len(self.escalations) > 0 or self.current_lane == LaneName.FULL

    def current_elapsed_seconds(self, now_ts: float | None = None) -> float:
        now = time.time() if now_ts is None else now_ts
        wall_delta = max(0.0, now - self.fast_started_at) if self.fast_started_at > 0 else 0.0
        return max(self.fast_elapsed_seconds, wall_delta)

    def to_fast_usage_dict(self, now_wall_ts: float | None = None) -> dict[str, Any]:
        elapsed = self.current_elapsed_seconds(now_wall_ts)
        return {
            "callsUsed": self.fast_calls_used,
            "inputTokens": self.fast_input_tokens,
            "outputTokens": self.fast_output_tokens,
            "startedWallTs": round(self.fast_started_at, 3),
            "elapsedSeconds": round(elapsed, 3),
            "subDeadlineSeconds": self.fast_sub_deadline_seconds,
        }

    @classmethod
    def from_checkpoint(
        cls,
        saved_vals: Mapping[str, Any],
        *,
        fallback_triage: TriageReport | None = None,
        now_wall_ts: float | None = None,
    ) -> LaneRuntimeState:
        """Reconstruct durable LaneRuntimeState from checkpoint state without recomputing triage or resetting budgets."""
        triage_raw = saved_vals.get("triage_report")
        if isinstance(triage_raw, Mapping) and triage_raw.get("candidateLane"):
            triage = TriageReport.from_dict(dict(triage_raw))
        elif fallback_triage is not None:
            triage = fallback_triage
        else:
            triage = TriageReport(candidate_lane=LaneName.FULL, risk_floor=RiskFloor.HIGH)

        escalations_raw = saved_vals.get("escalations") or ()
        escalations: list[EscalationDecision] = []
        seen_keys: set[tuple[str, str]] = set()
        for item in escalations_raw:
            if isinstance(item, Mapping):
                dec = EscalationDecision.from_dict(dict(item))
                key = (dec.reason.value, dec.detail)
                if key not in seen_keys:
                    seen_keys.add(key)
                    escalations.append(dec)

        saved_lane_str = str(saved_vals.get("lane", LaneName.FULL.value))
        current_lane = (
            LaneName.FULL
            if (saved_lane_str == LaneName.FULL.value or len(escalations) > 0)
            else LaneName.FAST
        )

        saved_risk_str = str(saved_vals.get("risk_floor") or triage.risk_floor.value)
        try:
            saved_risk = RiskFloor(saved_risk_str)
        except ValueError:
            saved_risk = RiskFloor.HIGH
        risk_floor = max_risk(triage.risk_floor, saved_risk)
        for esc in escalations:
            risk_floor = max_risk(risk_floor, esc.risk_floor)

        plan_raw = saved_vals.get("verification_plan")
        plan = (
            VerificationPlan.from_dict(dict(plan_raw))
            if isinstance(plan_raw, Mapping) and plan_raw.get("finalLane")
            else None
        )

        oracle_raw = saved_vals.get("oracle_evidence")
        oracle = (
            OracleEvidence.from_dict(dict(oracle_raw))
            if isinstance(oracle_raw, Mapping) and oracle_raw.get("level")
            else None
        )

        usage_raw = saved_vals.get("fast_usage")
        usage = dict(usage_raw) if isinstance(usage_raw, Mapping) else {}
        calls_used = max(
            int(usage.get("callsUsed", 0)),
            max((e.used_model_calls for e in escalations), default=0),
        )
        in_tokens = max(
            int(usage.get("inputTokens", 0)),
            max((e.used_input_tokens for e in escalations), default=0),
        )
        out_tokens = max(
            int(usage.get("outputTokens", 0)),
            max((e.used_output_tokens for e in escalations), default=0),
        )
        now = time.time() if now_wall_ts is None else now_wall_ts
        started_ts = float(usage.get("startedWallTs") or now)
        elapsed_sec = max(
            float(usage.get("elapsedSeconds", 0.0)),
            max((e.elapsed_seconds for e in escalations), default=0.0),
        )
        sub_deadline = float(usage.get("subDeadlineSeconds", FAST_SUB_DEADLINE_SECONDS))

        return cls(
            current_lane=current_lane,
            risk_floor=risk_floor,
            triage=triage,
            plan=plan,
            oracle=oracle,
            escalations=tuple(escalations),
            fast_calls_used=calls_used,
            fast_input_tokens=in_tokens,
            fast_output_tokens=out_tokens,
            fast_started_at=started_ts,
            fast_elapsed_seconds=elapsed_sec,
            fast_sub_deadline_seconds=sub_deadline,
        )


def record_fast_usage(
    state: LaneRuntimeState,
    calls: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    now_wall_ts: float | None = None,
) -> LaneRuntimeState:
    if calls < 0 or input_tokens < 0 or output_tokens < 0:
        raise ValueError("Fast-lane usage counters must be non-negative.")
    elapsed = state.current_elapsed_seconds(now_wall_ts)
    return replace(
        state,
        fast_calls_used=state.fast_calls_used + calls,
        fast_input_tokens=state.fast_input_tokens + input_tokens,
        fast_output_tokens=state.fast_output_tokens + output_tokens,
        fast_elapsed_seconds=elapsed,
    )


def check_fast_budget(
    state: LaneRuntimeState,
    additional_calls: int = 0,
    now_monotonic: float | None = None,
    task_remaining_seconds: float | None = None,
    *,
    now_wall_ts: float | None = None,
) -> tuple[bool, EscalationReason | None, str]:
    now = now_wall_ts if now_wall_ts is not None else (now_monotonic if now_monotonic is not None else time.time())
    elapsed = state.current_elapsed_seconds(now)
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
    now_wall_ts: float | None = None,
) -> tuple[LaneRuntimeState, EscalationDecision]:
    """Monotonically escalate from fast to full, preserving compact evidence references and used budgets."""
    if state.current_lane == LaneName.FULL and target_lane == LaneName.FAST:
        raise ValueError("Lane transition is strictly monotonic: cannot downgrade from full to fast.")
    if target_lane != LaneName.FULL:
        raise ValueError("Escalation target must be the full lane.")
    if new_risk_floor is not None and RISK_ORDER[new_risk_floor] < RISK_ORDER[state.risk_floor]:
        raise ValueError("Risk floor is strictly monotonic: cannot lower risk floor.")

    effective_floor = max_risk(state.risk_floor, new_risk_floor or RiskFloor.MEDIUM)
    now = now_wall_ts if now_wall_ts is not None else (now_monotonic if now_monotonic is not None else time.time())
    elapsed = state.current_elapsed_seconds(now)

    merged_evidence = dict(state.carried_evidence)
    merged_evidence["triage"] = state.triage.to_dict()
    if state.plan is not None:
        merged_evidence["verificationPlan"] = state.plan.to_dict()
    if state.oracle is not None:
        merged_evidence["oracle"] = state.oracle.to_dict()
    if evidence:
        merged_evidence.update(evidence)

    compact_preserved = _compact_evidence_summary(state.triage, state.plan, state.oracle, evidence)

    # Deduplicate if an identical escalation (reason + detail) was already recorded prior to a resume
    for existing in state.escalations:
        if existing.reason == reason and existing.detail == detail[:300]:
            updated_existing = replace(
                state,
                current_lane=LaneName.FULL,
                risk_floor=effective_floor,
                fast_elapsed_seconds=elapsed,
                carried_evidence=merged_evidence,
            )
            return updated_existing, existing

    decision = EscalationDecision(
        from_lane=state.current_lane,
        to_lane=LaneName.FULL,
        reason=reason,
        detail=detail[:300],
        risk_floor=effective_floor,
        preserved_evidence=compact_preserved,
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
        fast_elapsed_seconds=elapsed,
        carried_evidence=merged_evidence,
    )
    return updated, decision
