
from typing import Literal, TypedDict


class AgentState(TypedDict):
    """Shared state for the GitResolve agent workflow."""

    task_id: str
    repo_full_name: str
    issue_number: int
    issue_title: str
    issue_body: str

    stage: str
    problem_summary: str
    likely_files: list[str]
    plan: list[str]

    repair_attempts: int
    max_repair_attempts: int

    status: Literal[
        "planning",
        "awaiting_start",
        "running",
        "awaiting_approval",
        "completed",
        "failed",
    ]
    error: str | None
