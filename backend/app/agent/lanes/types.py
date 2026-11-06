"""Immutable typed contracts for deterministic Adaptive Lanes (AL)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class LaneName(str, Enum):
    FAST = "fast"
    FULL = "full"


class VerificationLevel(str, Enum):
    V0 = "V0"  # Image-owned compile/AST/undefined-name delta in Docker
    V1 = "V1"  # Isolated module import/smoke check in Docker after V0
    V2 = "V2"  # Targeted tests (reserved for future import-graph selection)
    V3 = "V3"  # Full regression-before/after + full test suite in Docker


class RiskFloor(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


RISK_ORDER: dict[RiskFloor, int] = {
    RiskFloor.LOW: 0,
    RiskFloor.MEDIUM: 1,
    RiskFloor.HIGH: 2,
}


def max_risk(a: RiskFloor, b: RiskFloor) -> RiskFloor:
    return a if RISK_ORDER[a] >= RISK_ORDER[b] else b


class ChangeClass(str, Enum):
    MISSING_IMPORT = "missing_import"
    UNUSED_IMPORT_REMOVAL = "unused_import_removal"
    MISSING_IMPORT_AND_UNUSED_REMOVAL = "missing_import_and_unused_removal"
    COMMENT_DOCSTRING_TYPO = "comment_docstring_typo"
    BEHAVIOR_CHANGE = "behavior_change"
    MULTI_FILE_CHANGE = "multi_file_change"
    RISKY_OVERRIDE = "risky_override"
    UNKNOWN = "unknown"


class EscalationReason(str, Enum):
    HARD_OVERRIDE = "hard_override"
    UNCERTAIN_LOCALIZATION = "uncertain_localization"
    NO_FAILING_ORACLE = "no_failing_oracle"
    DISALLOWED_CHANGE_CLASS = "disallowed_change_class"
    SCOPE_GROWTH = "scope_growth"
    ORACLE_DISAGREEMENT = "oracle_disagreement"
    VERIFICATION_FAILED = "verification_failed"
    REVIEW_REJECTED = "review_rejected"
    FAST_BUDGET_EXHAUSTED = "fast_budget_exhausted"
    MODEL_ESCALATED = "model_escalated"
    FLAKY_OR_INCONCLUSIVE = "flaky_or_inconclusive"


FAST_MAX_PROVIDER_CALLS = 3
FAST_SUB_DEADLINE_SECONDS = 120.0


@dataclass(frozen=True)
class TriageReport:
    candidate_lane: LaneName
    risk_floor: RiskFloor
    localized_files: tuple[str, ...] = ()
    undefined_names_by_file: dict[str, tuple[str, ...]] = field(default_factory=dict)
    unused_imports_by_file: dict[str, tuple[str, ...]] = field(default_factory=dict)
    signals: tuple[str, ...] = ()
    override_reasons: tuple[str, ...] = ()
    uncertain_localization: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidateLane": self.candidate_lane.value,
            "riskFloor": self.risk_floor.value,
            "localizedFiles": list(self.localized_files),
            "undefinedNamesByFile": {k: list(v) for k, v in self.undefined_names_by_file.items()},
            "unusedImportsByFile": {k: list(v) for k, v in self.unused_imports_by_file.items()},
            "signals": list(self.signals),
            "overrideReasons": list(self.override_reasons),
            "uncertainLocalization": self.uncertain_localization,
        }


@dataclass(frozen=True)
class PatchFacts:
    touched_files: tuple[str, ...]
    added_lines: int
    removed_lines: int
    change_class: ChangeClass
    added_imports: tuple[str, ...] = ()
    removed_imports: tuple[str, ...] = ()
    new_third_party_imports: tuple[str, ...] = ()
    resolved_undefined_names: tuple[str, ...] = ()
    referenced_removals: tuple[str, ...] = ()
    side_effect_removals: tuple[str, ...] = ()
    changed_exports: tuple[str, ...] = ()
    override_reasons: tuple[str, ...] = ()
    non_import_ast_unchanged: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "touchedFiles": list(self.touched_files),
            "addedLines": self.added_lines,
            "removedLines": self.removed_lines,
            "changeClass": self.change_class.value,
            "addedImports": list(self.added_imports),
            "removedImports": list(self.removed_imports),
            "newThirdPartyImports": list(self.new_third_party_imports),
            "resolvedUndefinedNames": list(self.resolved_undefined_names),
            "referencedRemovals": list(self.referenced_removals),
            "sideEffectRemovals": list(self.side_effect_removals),
            "changedExports": list(self.changed_exports),
            "overrideReasons": list(self.override_reasons),
            "nonImportAstUnchanged": self.non_import_ast_unchanged,
        }


@dataclass(frozen=True)
class VerificationPlan:
    final_lane: LaneName
    verification_level: VerificationLevel
    patch_facts: PatchFacts
    requires_v0: bool
    requires_v1: bool
    requires_full_suite: bool
    escalation_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "finalLane": self.final_lane.value,
            "verificationLevel": self.verification_level.value,
            "patchFacts": self.patch_facts.to_dict(),
            "requiresV0": self.requires_v0,
            "requiresV1": self.requires_v1,
            "requiresFullSuite": self.requires_full_suite,
            "escalationReasons": list(self.escalation_reasons),
        }


@dataclass(frozen=True)
class OracleEvidence:
    level: VerificationLevel
    original_failed: bool
    patched_passed: bool
    v0_passed: bool
    v1_passed: bool
    original_undefined_names: tuple[str, ...] = ()
    patched_undefined_names: tuple[str, ...] = ()
    removed_unused_imports: tuple[str, ...] = ()
    imported_modules: tuple[str, ...] = ()
    execution_ids: tuple[str, ...] = ()
    base_sha: str = ""
    diff_hash: str = ""
    summary: str = ""
    valid_for_fast_lane: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "originalFailed": self.original_failed,
            "patchedPassed": self.patched_passed,
            "v0Passed": self.v0_passed,
            "v1Passed": self.v1_passed,
            "originalUndefinedNames": list(self.original_undefined_names),
            "patchedUndefinedNames": list(self.patched_undefined_names),
            "removedUnusedImports": list(self.removed_unused_imports),
            "importedModules": list(self.imported_modules),
            "executionIds": list(self.execution_ids),
            "baseSha": self.base_sha,
            "diffHash": self.diff_hash,
            "summary": self.summary,
            "validForFastLane": self.valid_for_fast_lane,
        }


@dataclass(frozen=True)
class EscalationDecision:
    from_lane: LaneName
    to_lane: LaneName
    reason: EscalationReason
    detail: str
    risk_floor: RiskFloor = RiskFloor.HIGH
    preserved_evidence: dict[str, Any] = field(default_factory=dict)
    used_model_calls: int = 0
    used_input_tokens: int = 0
    used_output_tokens: int = 0
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "fromLane": self.from_lane.value,
            "toLane": self.to_lane.value,
            "reason": self.reason.value,
            "detail": self.detail,
            "riskFloor": self.risk_floor.value,
            "preservedEvidence": dict(self.preserved_evidence),
            "usedModelCalls": self.used_model_calls,
            "usedInputTokens": self.used_input_tokens,
            "usedOutputTokens": self.used_output_tokens,
            "elapsedSeconds": round(self.elapsed_seconds, 3),
        }
