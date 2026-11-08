"""Deterministic failure classifier, strategy table, and bounded attempt memory (Phase 3)."""
from __future__ import annotations

from enum import Enum
import hashlib
import json
import re
from typing import Any, Mapping

from sandbox.classification import INFRA_CATEGORIES, classify as classify_sandbox_result


class FailureCategory(str, Enum):
    """Canonical single-source-of-truth failure taxonomy across sandbox, full lane, and fast lane."""

    ENV_MISSING_DEP = "env_missing_dep"
    ENV_MISSING_SECRET = "env_missing_secret"
    ANCHOR_MISMATCH = "anchor_mismatch"
    NO_REPRO = "no_repro"
    LOGIC_FAIL = "logic_fail"
    REGRESSION_ELSEWHERE = "regression_elsewhere"
    TIMEOUT_FLAKY = "timeout_flaky"
    SANDBOX_INFRA = "sandbox_infra"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TOOL_INVALID = "tool_invalid"
    POLICY_BLOCK = "policy_block"
    UNKNOWN_INTERNAL = "unknown_internal"
    # Fast-lane specific deterministic categories
    STATIC_ORACLE_FAIL = "static_oracle_fail"
    NO_ORACLE = "no_oracle"
    RISK_OVERRIDE = "risk_override"
    FAST_BUDGET_EXHAUSTED = "fast_budget_exhausted"


INFRA_FAILURE_CATEGORIES: frozenset[str] = frozenset(
    {
        FailureCategory.ENV_MISSING_DEP.value,
        FailureCategory.ENV_MISSING_SECRET.value,
        FailureCategory.SANDBOX_INFRA.value,
        FailureCategory.PROVIDER_UNAVAILABLE.value,
    }
)

FAST_ESCALATION_CATEGORIES: frozenset[str] = frozenset(
    {
        FailureCategory.LOGIC_FAIL.value,
        FailureCategory.STATIC_ORACLE_FAIL.value,
        FailureCategory.NO_ORACLE.value,
        FailureCategory.NO_REPRO.value,
        FailureCategory.REGRESSION_ELSEWHERE.value,
        FailureCategory.RISK_OVERRIDE.value,
        FailureCategory.FAST_BUDGET_EXHAUSTED.value,
    }
)

# Direct mapping from sandbox/classification.py outputs (single source of truth, no duplicate regexes)
SANDBOX_CLASS_TO_FAILURE_CATEGORY: dict[str, str] = {
    "missing_dep": FailureCategory.ENV_MISSING_DEP.value,
    "invalid_dependency_spec": FailureCategory.ENV_MISSING_DEP.value,
    "missing_secret": FailureCategory.ENV_MISSING_SECRET.value,
    "sandbox_prep_failure": FailureCategory.SANDBOX_INFRA.value,
    "timeout_resource": FailureCategory.TIMEOUT_FLAKY.value,
    "no_tests": FailureCategory.NO_REPRO.value,
    "collection_error": FailureCategory.NO_REPRO.value,
    "preexisting_test_failure": FailureCategory.LOGIC_FAIL.value,
}

# Direct mapping from Adaptive Lanes EscalationReason values (read-only mapping; reasons live in app.agent.lanes)
ESCALATION_REASON_TO_FAILURE_CATEGORY: dict[str, str] = {
    "hard_override": FailureCategory.RISK_OVERRIDE.value,
    "disallowed_change_class": FailureCategory.RISK_OVERRIDE.value,
    "scope_growth": FailureCategory.RISK_OVERRIDE.value,
    "uncertain_localization": FailureCategory.RISK_OVERRIDE.value,
    "no_failing_oracle": FailureCategory.NO_ORACLE.value,
    "flaky_or_inconclusive": FailureCategory.NO_ORACLE.value,
    "oracle_disagreement": FailureCategory.STATIC_ORACLE_FAIL.value,
    "verification_failed": FailureCategory.STATIC_ORACLE_FAIL.value,
    "fast_budget_exhausted": FailureCategory.FAST_BUDGET_EXHAUSTED.value,
    "review_rejected": FailureCategory.LOGIC_FAIL.value,
    "model_escalated": FailureCategory.LOGIC_FAIL.value,
}


def fingerprint(candidate: Any) -> str:
    parsed = getattr(candidate, "parsed", candidate)
    text = (
        parsed.model_dump_json()
        if hasattr(parsed, "model_dump_json")
        else str(getattr(candidate, "raw_text", parsed))
    )
    try:
        data = json.loads(text)
        # Rewording an explanation is not a different implementation strategy.
        keys = {"edits", "tests", "reproduction_diff", "final_diff", "diff"}
        if isinstance(data, dict) and keys.intersection(data):
            data = {k: v for k, v in data.items() if k in keys}
        text = json.dumps(data, sort_keys=True)
    except (ValueError, TypeError):
        pass
    return hashlib.sha256(text.encode()).hexdigest()


def _extract_failed_test_files(text: str) -> list[str]:
    """Extract failing test file paths from pytest short summary / traceback lines."""
    files: list[str] = []
    for match in re.finditer(r"(?:FAILED|ERROR)\s+([^\s:]+?\.py)(?:::|\s|$)", text):
        path = match.group(1).strip().replace("\\", "/")
        if path not in files:
            files.append(path)
    return files


def classify_failure(
    message: str = "",
    *,
    exec_result: Any | None = None,
    escalation_reason: Any | None = None,
    stage: str = "",
    target_test_files: tuple[str, ...] | list[str] = (),
    baseline_failed_files: tuple[str, ...] | list[str] = (),
) -> str:
    """Deterministically classify a failure from exit codes, sandbox results, tracebacks, or AL escalation reasons."""
    # 1. Map Adaptive Lanes EscalationReason directly if provided
    if escalation_reason is not None:
        reason_val = getattr(escalation_reason, "value", str(escalation_reason)).strip().lower()
        if reason_val in ESCALATION_REASON_TO_FAILURE_CATEGORY:
            return ESCALATION_REASON_TO_FAILURE_CATEGORY[reason_val]

    # 2. Map sandbox/classification.py output directly if an execution result is provided
    if exec_result is not None:
        sb_cat = classify_sandbox_result(exec_result)
        if sb_cat in INFRA_CATEGORIES:
            return SANDBOX_CLASS_TO_FAILURE_CATEGORY[sb_cat]
        combined_out = (getattr(exec_result, "stdout", "") or "") + "\n" + (getattr(exec_result, "stderr", "") or "")
        if "ORACLE_V0_" in combined_out or "ORACLE_V1_" in combined_out:
            if getattr(exec_result, "exit_code", 1) != 0:
                return FailureCategory.STATIC_ORACLE_FAIL.value
        if sb_cat == "collection_error":
            return (
                FailureCategory.NO_REPRO.value
                if stage in {"reproduce", "baseline"}
                else FailureCategory.LOGIC_FAIL.value
            )
        if sb_cat == "no_tests":
            return FailureCategory.NO_REPRO.value
        if sb_cat == "passed" and stage == "reproduce":
            return FailureCategory.NO_REPRO.value
        if sb_cat == "preexisting_test_failure":
            if stage == "reproduce":
                if "ImportError:" in combined_out or "SyntaxError:" in combined_out:
                    return FailureCategory.NO_REPRO.value
            failed_files = _extract_failed_test_files(combined_out)
            norm_targets = {t.replace("\\", "/").lstrip("./") for t in target_test_files if t}
            norm_baseline = {b.replace("\\", "/").lstrip("./") for b in baseline_failed_files if b}
            if failed_files and norm_targets:
                unrelated = [
                    f for f in failed_files
                    if f.lstrip("./") not in norm_targets and f.lstrip("./") not in norm_baseline
                ]
                if unrelated:
                    return FailureCategory.REGRESSION_ELSEWHERE.value
            return FailureCategory.LOGIC_FAIL.value

    text = (message or "").strip()
    lower = text.lower()

    # 3. Check explicit fast-lane / oracle markers in message
    if "oracle_inconclusive" in lower or "no_failing_oracle" in lower or "no failing static oracle" in lower:
        return FailureCategory.NO_ORACLE.value
    if "static_oracle_fail" in lower or "oracle_disagreement" in lower or "patched code still" in lower:
        return FailureCategory.STATIC_ORACLE_FAIL.value
    if "fast_budget_exhausted" in lower or "fast sub-deadline" in lower or "fast-lane call budget" in lower:
        return FailureCategory.FAST_BUDGET_EXHAUSTED.value
    if "risk_override" in lower or "hard_override" in lower or "disallowed_change_class" in lower or "scope_growth" in lower:
        return FailureCategory.RISK_OVERRIDE.value

    # 4. Check sandbox/infra/provider patterns
    if any(
        term in lower
        for term in (
            "modulenotfounderror",
            "no module named",
            "distributionnotfound",
            "packagenotfounderror",
            "invalidrequirement",
            "invalid dependency",
            "undeclared runtime dependenc",
        )
    ):
        return FailureCategory.ENV_MISSING_DEP.value
    if any(
        term in lower
        for term in (
            "streamlitsecretnotfounderror",
            "no secrets found",
            "missing required api key",
            "missing api key",
            "missing secret",
            "secret not set",
        )
    ) or re.search(r"keyerror:\s*['\"][a-z0-9_]*(?:key|token|secret|password)[a-z0-9_]*['\"]", lower):
        return FailureCategory.ENV_MISSING_SECRET.value
    if any(
        term in lower
        for term in (
            "provider error",
            "all configured providers failed",
            "model service",
            "rate limit",
            "quota exceeded",
            "llm_unavailable",
            "provider health circuit",
        )
    ):
        return FailureCategory.PROVIDER_UNAVAILABLE.value
    if any(
        term in lower
        for term in (
            "sandbox_prep_failure",
            "container preparation failed",
            "docker daemon",
            "cleanup_ok=false",
            "exit_code=125",
        )
    ):
        return FailureCategory.SANDBOX_INFRA.value
    if any(term in lower for term in ("timed out", "timeout", "oom_killed", "flaky")):
        return FailureCategory.TIMEOUT_FLAKY.value

    # 5. Policy / scope / anchor / tool validation
    if any(
        term in lower
        for term in (
            "protected",
            "unsafe",
            "unsupported file operation",
            "outside the approved safety scope",
            "policy_violation",
            "scope",
            "changed lines",
            "exceeds max",
        )
    ):
        return FailureCategory.POLICY_BLOCK.value
    if any(
        term in lower
        for term in (
            "match original",
            "anchor",
            "exactly once",
            "ambiguous",
            "old_text",
            "search block not found",
        )
    ):
        return FailureCategory.ANCHOR_MISMATCH.value
    if any(
        term in lower
        for term in (
            "unsupported argument",
            "unknown tool",
            "malformed tool",
            "invalid tool",
            "validation error for repairactionenvelope",
            "did not satisfy the required structured output",
        )
    ):
        return FailureCategory.TOOL_INVALID.value

    # 6. Reproduction vs regression elsewhere vs logic failure
    if any(
        term in lower
        for term in (
            "did not reproduce",
            "reproduction did not",
            "error collecting",
            "no tests ran",
            "regression section must be identical",
        )
    ):
        return FailureCategory.NO_REPRO.value

    if "regression_elsewhere" in lower or "existing test" in lower and "broke" in lower:
        return FailureCategory.REGRESSION_ELSEWHERE.value

    failed_files = _extract_failed_test_files(text)
    if failed_files and target_test_files:
        norm_targets = {t.replace("\\", "/").lstrip("./") for t in target_test_files if t}
        norm_baseline = {b.replace("\\", "/").lstrip("./") for b in baseline_failed_files if b}
        unrelated = [
            f for f in failed_files
            if f.lstrip("./") not in norm_targets and f.lstrip("./") not in norm_baseline
        ]
        if unrelated:
            return FailureCategory.REGRESSION_ELSEWHERE.value

    if any(
        term in lower
        for term in (
            "test suite",
            "failed",
            "assertionerror",
            "quality review",
            "requirement",
            "blocking_issues",
            "traceback",
        )
    ):
        return FailureCategory.LOGIC_FAIL.value

    return FailureCategory.UNKNOWN_INTERNAL.value


def category(message: str, **kwargs: Any) -> str:
    """Backward-compatible entry point returning the canonical deterministic failure category."""
    return classify_failure(message, **kwargs)


STRATEGIES: dict[str, str] = {
    FailureCategory.ENV_MISSING_DEP.value: (
        "Re-prepare the sandbox environment or use only packages declared in requirements.txt/pyproject.toml. "
        "Do not introduce undeclared third-party imports."
    ),
    FailureCategory.ENV_MISSING_SECRET.value: (
        "Stop and request user action: required runtime credentials or secrets are unavailable in the network-isolated sandbox."
    ),
    FailureCategory.ANCHOR_MISMATCH.value: (
        "Re-read the actual original source around the target lines before editing. "
        "Use a short, unique, verbatim old_text anchor with exact indentation; never reconstruct source from memory."
    ),
    FailureCategory.NO_REPRO.value: (
        "Expand exploration and revise the fault hypothesis. Choose a behavioral assertion that fails on the original "
        "code for the reported reason without module-scope import or collection errors."
    ),
    FailureCategory.LOGIC_FAIL.value: (
        "Inspect the failed assertion, traceback, and implementation callers. Address the first concrete failure and its "
        "root cause. Change the implementation/test strategy instead of repeating the failed proposal."
    ),
    FailureCategory.REGRESSION_ELSEWHERE.value: (
        "Your patch broke existing callers or unrelated tests. Inspect references to every modified function/signature "
        "and preserve backward-compatible behavior across callers."
    ),
    FailureCategory.TIMEOUT_FLAKY.value: (
        "Avoid infinite loops, blocking I/O, or expensive sleeps. Keep execution deterministic and fast."
    ),
    FailureCategory.SANDBOX_INFRA.value: (
        "Sandbox infrastructure error encountered. Pause and requeue without charging a repair attempt."
    ),
    FailureCategory.PROVIDER_UNAVAILABLE.value: (
        "Model provider temporarily unavailable. Pause and requeue without charging a repair attempt."
    ),
    FailureCategory.TOOL_INVALID.value: (
        "Return a valid structured tool call or EditProposal matching the exact per-tool argument schema."
    ),
    FailureCategory.POLICY_BLOCK.value: (
        "This operation is outside the approved safety or diff scope. Use fewer, smaller source edits on allowed files "
        "only, or report blocked_reason if a narrower plan is required."
    ),
    FailureCategory.UNKNOWN_INTERNAL.value: (
        "Return valid structured output with exact original source anchors; report blocked_reason when the request cannot be safely completed."
    ),
    FailureCategory.STATIC_ORACLE_FAIL.value: (
        "Patched code still failed the static/import oracle. Escalate to the full lane and verify with pytest."
    ),
    FailureCategory.NO_ORACLE.value: (
        "Static oracle was inconclusive or absent. Escalate to the full lane and write a behavioral regression test."
    ),
    FailureCategory.RISK_OVERRIDE.value: (
        "Patch triggered a risk floor override. Escalate to the full lane for complete regression and test suite verification."
    ),
    FailureCategory.FAST_BUDGET_EXHAUSTED.value: (
        "Fast-lane sub-deadline or call budget exhausted. Escalate to the full lane carrying prior evidence."
    ),
    # Legacy aliases preserved for backward compatibility with older serialized attempt records
    "scope": "Use fewer, smaller source edits and concise meaningful tests. Do not omit required behavior, empty existing files, or change limits. Report blocked_reason if the minimum safe change cannot fit.",
    "anchor": "Re-read the actual original source. Use a short unique exact anchor; do not reconstruct Unicode or spacing from memory.",
    "protected": "This operation is not allowed. Do not disguise it as another edit or try to bypass policy.",
    "regression": "Choose a different behavioral assertion that fails on the original for the requested reason. Avoid collection/import errors and mocks that hide the implementation.",
    "tests": "Address the first concrete failure and its cause. Change the implementation/test strategy instead of repeating the failed proposal.",
    "review": "Address the specific unmet requirement with implementation and real evidence. Never relabel incomplete checks as passed.",
    "format": "Return valid structured output with exact original source anchors; report blocked_reason when the request cannot be safely completed.",
}


def strategy_for(cat: str) -> str:
    """Return the required strategy directive for a deterministic failure category."""
    return STRATEGIES.get(cat, STRATEGIES[FailureCategory.UNKNOWN_INTERNAL.value])


class AttemptMemory:
    """Bounded structured memory of failed repair attempts for strategy routing and Argon escalation."""

    def __init__(self, records: list[dict[str, Any]] | tuple[dict[str, Any], ...] = ()) -> None:
        self.records: list[dict[str, Any]] = list(records)[-6:]

    def record(
        self,
        candidate: Any,
        message: str,
        *,
        exec_result: Any | None = None,
        escalation_reason: Any | None = None,
        stage: str = "",
        target_test_files: tuple[str, ...] | list[str] = (),
        baseline_failed_files: tuple[str, ...] | list[str] = (),
        what_was_tried: str = "",
        evidence: str = "",
        rejected_hypothesis: str = "",
    ) -> dict[str, Any]:
        cat = classify_failure(
            message,
            exec_result=exec_result,
            escalation_reason=escalation_reason,
            stage=stage,
            target_test_files=target_test_files,
            baseline_failed_files=baseline_failed_files,
        )
        feedback = message if len(message) <= 2200 else message[:1000] + "\n…\n" + message[-1200:]
        parsed = getattr(candidate, "parsed", candidate)
        tried_summary = (
            what_was_tried
            or getattr(parsed, "explanation", "")
            or (str(parsed)[:200] if parsed is not None else "candidate patch")
        )[:240]
        req_change = STRATEGIES.get(cat, STRATEGIES[FailureCategory.UNKNOWN_INTERNAL.value])
        item: dict[str, Any] = {
            "fingerprint": fingerprint(candidate),
            "category": cat,
            "feedback": feedback,
            "what_was_tried": tried_summary,
            "evidence": (evidence or feedback[:300])[:300],
            "why_it_failed": f"{cat}: {feedback[:240]}",
            "rejected_hypothesis": (rejected_hypothesis or tried_summary)[:240],
            "required_change": req_change,
        }
        self.records = (self.records + [item])[-6:]
        return item

    def repeated(self, candidate: Any) -> bool:
        fp = fingerprint(candidate)
        return any(r.get("fingerprint") == fp for r in self.records)

    def persistent(self) -> bool:
        return (
            len(self.records) >= 2
            and self.records[-1].get("category") == self.records[-2].get("category")
        )

    def consecutive_logic_failures(self) -> int:
        count = 0
        for rec in reversed(self.records):
            if rec.get("category") in {
                FailureCategory.LOGIC_FAIL.value,
                FailureCategory.REGRESSION_ELSEWHERE.value,
                "tests",
                "review",
            }:
                count += 1
            else:
                break
        return count

    def compact_trajectory(self, max_items: int = 4) -> list[dict[str, Any]]:
        """Return compact structured failure summaries suitable for Argon reflection prompts and checkpoints."""
        out: list[dict[str, Any]] = []
        for rec in self.records[-max_items:]:
            out.append({
                "fingerprint": str(rec.get("fingerprint", ""))[:16],
                "category": rec.get("category", ""),
                "whatWasTried": str(rec.get("what_was_tried", ""))[:180],
                "whyItFailed": str(rec.get("why_it_failed", rec.get("feedback", "")))[:220],
                "rejectedHypothesis": str(rec.get("rejected_hypothesis", ""))[:180],
                "requiredChange": str(rec.get("required_change", ""))[:200],
            })
        return out

    def prompt(self) -> str:
        if not self.records:
            return "No previous failures."
        last_cat = self.records[-1].get("category", FailureCategory.UNKNOWN_INTERNAL.value)
        strategy = STRATEGIES.get(last_cat, STRATEGIES[FailureCategory.UNKNOWN_INTERNAL.value])
        return (
            json.dumps(self.records, ensure_ascii=True)
            + "\nRequired strategy change: "
            + strategy
            + " Never repeat an identical rejected proposal."
        )
