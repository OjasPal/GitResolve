from datetime import datetime
from typing import Any

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel
from app.schemas.enums import (
    Action,
    ApprovalState,
    DispatchPhase,
    FailureCode,
    TaskStage,
    TaskStatus,
)

REPO_ID_PATTERN = r"^\d+_\d+$"
DIFF_HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"


# --- Requests ----------------------------------------------------------------
class CreateTaskRequest(RequestModel):
    repo_id: str = Field(pattern=REPO_ID_PATTERN)
    issue_number: int = Field(gt=0)


class ApproveRequest(RequestModel):
    diff_hash: str = Field(pattern=DIFF_HASH_PATTERN)


# --- Shared nested shapes (also validated on worker writes) --------------------
class IssueComment(ResponseModel):
    author: str | None = None
    body: str = Field(max_length=2048)


class IssueView(ResponseModel):
    number: int
    title: str | None = None
    url: str | None = None
    body: str | None = None
    body_truncated: bool = False
    labels: list[str] = Field(default_factory=list, max_length=20)
    comments: list[IssueComment] = Field(default_factory=list, max_length=10)


class IssueSummary(ResponseModel):
    number: int
    title: str | None = None
    url: str | None = None
    labels: list[str] = Field(default_factory=list)


class PlanData(ResponseModel):
    """Plan contract between the agent and the API/UI."""

    problem_summary: str
    steps: list[str] = Field(min_length=1, max_length=10)
    likely_files: list[str] = Field(default_factory=list, max_length=20)
    questions: list[str] = Field(default_factory=list, max_length=10)


class Attempts(ResponseModel):
    used: int = 0
    max: int


class Budgets(ResponseModel):
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    exec_seconds: float = 0


class Limits(ResponseModel):
    max_repair_attempts: int
    max_model_calls: int
    task_timeout_seconds: int
    execution_timeout_seconds: int
    max_diff_files: int
    max_diff_lines: int


class Failure(ResponseModel):
    code: FailureCode
    message: str = Field(max_length=500)
    phase: DispatchPhase | None = None
    retryable: bool = False


class Approval(ResponseModel):
    state: ApprovalState
    by: str | None = None
    at: datetime | None = None
    diff_hash: str | None = None


class PullRequestView(ResponseModel):
    number: int
    url: str
    branch: str
    created_at: datetime | None = None


# --- Responses ---------------------------------------------------------------
class TaskResponse(ResponseModel):
    id: str
    status: TaskStatus
    stage: TaskStage
    repo_id: str
    repo_full_name: str
    default_branch: str
    base_sha: str | None = None
    issue: IssueView
    plan: PlanData | None = None
    reproduction: dict[str, Any] | None = None
    baseline: dict[str, Any] | None = None
    attempts: Attempts
    budgets: Budgets
    limits: Limits
    final_diff_hash: str | None = None
    final_diff_summary: dict[str, Any] | None = None
    approval: Approval | None = None
    failure: Failure | None = None
    result: dict[str, Any] | None = None
    pr: PullRequestView | None = None
    cancel_requested: bool = False
    available_actions: list[Action] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    status_updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    deadline_at: datetime | None = None

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> "TaskResponse":
        from app.services.lifecycle import available_actions

        failure = doc.get("failure") or {}
        data = dict(doc)
        data["availableActions"] = available_actions(doc["status"], failure.get("phase"))
        return cls.model_validate(data)


class TaskSummary(ResponseModel):
    id: str
    status: TaskStatus
    stage: TaskStage
    repo_id: str
    repo_full_name: str
    issue: IssueSummary
    attempts: Attempts
    pr: PullRequestView | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> "TaskSummary":
        return cls.model_validate(doc)


class TaskListResponse(ResponseModel):
    items: list[TaskSummary]
    next_cursor: str | None = None
