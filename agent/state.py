from typing import Any, Literal, NotRequired, TypedDict


class AgentState(TypedDict):
    """Shared state for the GitResolve agent workflow."""

    task_id: str
    repo_full_name: str
    issue_number: int | None
    issue_title: str
    issue_body: str

    base_sha: NotRequired[str | None]
    repo_path: NotRequired[str | None]
    pr_url: NotRequired[str]
    pr_branch: NotRequired[str]
    pr_number: NotRequired[int]

    stage: str
    problem_summary: str
    likely_files: list[str]
    plan: list[str]
    needs_more_information: bool
    questions_for_reporter: list[str]

    repair_attempts: int
    max_repair_attempts: int

    # Routing and Observability metadata
    selected_provider: NotRequired[str | None]
    selected_model: NotRequired[str | None]
    failover_occurred: NotRequired[bool]
    specialist_escalation_occurred: NotRequired[bool]
    provider_retries: NotRequired[int]
    model_switch_reason: NotRequired[str | None]
    token_usage: NotRequired[dict[str, Any] | None]
    provider_error_details: NotRequired[list[dict[str, Any]]]
    routing_metadata: NotRequired[dict[str, Any]]

    # Specialist reasoning escalation tracking
    escalation_count: NotRequired[int]
    escalation_reason: NotRequired[str | None]
    is_complex_issue: NotRequired[bool]

    status: Literal[
        "planning",
        "awaiting_start",
        "running",
        "awaiting_approval",
        "completed",
        "failed",
    ]
    error: str | None
