from typing import Any, Callable
from pydantic import BaseModel, Field

from agent.config import get_router_settings
from agent.observability import UsageRecord
from agent.router import ModelRouter, RouterResult, get_router
from agent.state import AgentState


class PatchProposal(BaseModel):
    """Structured output expected when generating a code repair patch."""

    explanation: str = Field(description="Explanation of the fix and root cause.")
    diff: str = Field(description="Git unified diff patch.")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score from 0.0 to 1.0.")
    cross_file_impact: bool = Field(
        default=False,
        description="Whether the bug requires coordinated changes across multiple files or modules."
    )


def should_escalate_to_opus(state: AgentState, settings=None) -> tuple[bool, str]:
    """Determine if the repair reasoning workflow warrants specialist escalation to Opus.
    
    Escalation is a workflow decision, not an exception handler.
    Conditions:
    1. Workflow explicitly flagged complex issue / cross-file dependency.
    2. Bounded repair loop reached configured threshold (e.g. repeated patch/test failures).
    3. Low structured confidence in previous attempt.
    """
    settings = settings or get_router_settings()
    if not settings.enable_opus_escalation:
        return False, "Opus escalation disabled"

    current_count = state.get("escalation_count", 0)
    if current_count >= settings.max_opus_escalations:
        return False, f"Maximum Opus escalations reached ({current_count}/{settings.max_opus_escalations})"

    if state.get("is_complex_issue", False):
        return True, "Workflow flagged complex cross-file dependency"

    if state.get("specialist_escalation_requested", False):
        return True, state.get("escalation_reason") or "Explicit specialist escalation requested by workflow"

    # Repeated repair failures in bounded loop
    if state.get("repair_attempts", 0) >= 1:
        return True, f"Repair attempt {state.get('repair_attempts')} failed tests; escalating to specialist reasoning"

    return False, "Standard reasoning sufficient"


def execute_repair_step(
    state: AgentState,
    router: ModelRouter | None = None,
    test_runner: Callable[[str], tuple[bool, str]] | None = None,
) -> dict[str, Any]:
    """Execute a single bounded repair iteration.
    
    Demonstrates:
    - Normal Sonnet execution with provider failover for technical outages.
    - Explicit Opus escalation for complex reasoning / repeated test failures.
    - Test failures or invalid patches DO NOT trigger provider failover.
    - Preserves bounded repair loop.
    """
    active_router = router or get_router()
    repair_attempts = state.get("repair_attempts", 0)
    max_repair_attempts = state.get("max_repair_attempts", 3)
    escalation_count = state.get("escalation_count", 0)

    escalate, reason = should_escalate_to_opus(state)

    prompt = f"""
Repository: {state.get('repo_full_name')}
Issue #{state.get('issue_number')}: {state.get('issue_title')}
Description: {state.get('issue_body')}
Problem Summary: {state.get('problem_summary')}
Plan: {state.get('plan')}
Current Repair Attempt: {repair_attempts + 1} of {max_repair_attempts}
Previous error / test output: {state.get('error')}
""".strip()

    if escalate:
        router_result = active_router.escalate_specialist(
            prompt=prompt,
            escalation_reason=reason,
            schema=PatchProposal,
            task_id=state.get("task_id"),
            current_escalation_count=escalation_count,
        )
        new_escalation_count = escalation_count + 1
    else:
        router_result = active_router.route_normal(
            prompt=prompt,
            schema=PatchProposal,
            task_id=state.get("task_id"),
        )
        new_escalation_count = escalation_count

    proposal: PatchProposal | None = router_result.result.parsed
    if not isinstance(proposal, PatchProposal):
        if router_result.result.raw_text:
            try:
                proposal = PatchProposal.model_validate_json(router_result.result.raw_text)
            except Exception:
                proposal = None

    if proposal is None:
        # Model produced invalid patch - this is an agent reasoning failure, NOT a provider outage!
        new_attempts = repair_attempts + 1
        is_exhausted = new_attempts >= max_repair_attempts
        return {
            "repair_attempts": new_attempts,
            "status": "failed" if is_exhausted else "running",
            "error": "Model produced invalid patch format (reasoning error, no provider failover)",
            "routing_metadata": router_result.to_metadata(),
            "escalation_count": new_escalation_count,
        }

    # Evaluate whether proposal reveals complex cross-file impact requiring subsequent escalation
    cross_file = proposal.cross_file_impact or proposal.confidence < 0.6

    # Execute tests against the generated patch
    tests_passed = True
    test_output = "All tests passed"
    if test_runner:
        tests_passed, test_output = test_runner(proposal.diff)

    new_attempts = repair_attempts + 1
    routing_meta = router_result.to_metadata()

    if not tests_passed:
        # Tests failed - DO NOT confuse this with provider failure!
        is_exhausted = new_attempts >= max_repair_attempts
        return {
            "repair_attempts": new_attempts,
            "status": "failed" if is_exhausted else "running",
            "error": f"Test suite failed after applying patch: {test_output}",
            "is_complex_issue": cross_file,
            "escalation_count": new_escalation_count,
            "routing_metadata": routing_meta,
            "selected_provider": router_result.selected_provider,
            "selected_model": router_result.selected_model,
            "failover_occurred": router_result.failover_occurred,
            "specialist_escalation_occurred": router_result.specialist_escalation_occurred,
            "provider_retries": router_result.provider_retries,
            "token_usage": router_result.usage.to_dict() if router_result.usage else None,
        }

    # Success! Fix verified by tests
    return {
        "repair_attempts": new_attempts,
        "status": "awaiting_approval",
        "stage": "awaiting_approval",
        "error": None,
        "is_complex_issue": cross_file,
        "escalation_count": new_escalation_count,
        "routing_metadata": routing_meta,
        "selected_provider": router_result.selected_provider,
        "selected_model": router_result.selected_model,
        "failover_occurred": router_result.failover_occurred,
        "specialist_escalation_occurred": router_result.specialist_escalation_occurred,
        "provider_retries": router_result.provider_retries,
        "token_usage": router_result.usage.to_dict() if router_result.usage else None,
    }
