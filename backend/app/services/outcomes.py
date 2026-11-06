"""Deterministic public outcomes; task statuses and failure codes stay stable."""
from app.schemas.enums import FailureCode, TaskStatus
from app.core.redaction import redact_value

OUTCOME_CODES = {
    "needs_user_action": FailureCode.ACCESS_REVOKED,
    "needs_clarification": FailureCode.ISSUE_AMBIGUOUS,
    "cannot_reproduce": FailureCode.NOT_REPRODUCED,
    "out_of_scope": FailureCode.POLICY_VIOLATION,
    "cannot_fix_with_report": FailureCode.NO_FIX_FOUND,
    "infra_retry": FailureCode.INTERNAL_ERROR,
}
NEXT_STEPS = {
    "fix_ready": "Review the exact diff and approve it before publication.",
    "needs_user_action": "Restore repository access, then run again.",
    "needs_clarification": "Provide reproduction steps and expected behavior.",
    "cannot_reproduce": "Provide a failing example and its environment requirements.",
    "out_of_scope": "Narrow the request to changes allowed by the repository policy.",
    "cannot_fix_with_report": "Review the evidence and refine the request before running again.",
    "infra_retry": "The worker will retry automatically within the task deadline.",
}

def outcome_for(code):
    return {
        "access_revoked": "needs_user_action", "issue_ambiguous": "needs_clarification",
        "not_reproduced": "cannot_reproduce", "policy_violation": "out_of_scope",
    }.get(str(code), "cannot_fix_with_report")


def outcome_payload(kind, reason, explanation, evidence=(), next_step=""):
    if kind not in NEXT_STEPS:
        raise ValueError("Unknown structured outcome.")
    return redact_value({"kind": kind, "reasonCode": str(reason),
        "explanation": explanation, "evidence": list(evidence),
        "nextStep": next_step or NEXT_STEPS[kind]})


def public_outcome(doc):
    if doc.get("outcome"):
        return doc["outcome"]
    if doc.get("status") == TaskStatus.AWAITING_APPROVAL:
        ver = doc.get("verification") or {}
        if ver.get("lane") == "fast" and ver.get("testsRun") == 0:
            return outcome_payload("fix_ready", "static_verification_passed", "Static verification passed; review the exact diff before publication.")
        return outcome_payload("fix_ready", "validated_fix", "A validated fix is ready for review.")
    failure = doc.get("failure")
    if failure:
        return outcome_payload(outcome_for(failure["code"]), failure["code"], failure["message"])
    return None
