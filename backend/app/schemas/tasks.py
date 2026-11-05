from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import Field, model_validator

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
    issue_number: int | None = Field(default=None, gt=0)
    prompt: str | None = Field(default=None, min_length=1, max_length=20_000)

    attachment_ids: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def validate_source(self):
        if self.prompt is not None:
            self.prompt = self.prompt.strip()
        if bool(self.prompt) == (self.issue_number is not None):
            raise ValueError("Provide either a bug description or an existing GitHub issue, exclusively.")
        return self


class GateChatRequest(RequestModel):
    message: str = Field(min_length=1, max_length=20000)
    operation: Literal["ask", "revise", "prompt"] = "ask"
    revision: int = Field(default=0, ge=0)
    request_id: str = Field(pattern=r"^[a-zA-Z0-9-]{16,64}$")

    @model_validator(mode="after")
    def validate_message(self):
        self.message = self.message.strip()
        if not self.message or (self.operation != "prompt" and len(self.message) > 4000):
            raise ValueError("Write a message of up to 4,000 characters, or edit the request using its dedicated editor.")
        return self

class GateMessage(ResponseModel):
    role: Literal["user", "assistant"]
    content: str
    gate: str

class StartRequest(RequestModel):
    revision: int = Field(default=0, ge=0)


class ApproveRequest(RequestModel):
    revision: int = Field(default=0, ge=0)
    diff_hash: str = Field(pattern=DIFF_HASH_PATTERN)


# --- Shared nested shapes (also validated on worker writes) --------------------
class IssueComment(ResponseModel):
    author: str | None = None
    body: str = Field(max_length=2048)


class IssueView(ResponseModel):
    number: int | None
    title: str | None = None
    url: str | None = None
    body: str | None = None
    body_truncated: bool = False
    labels: list[str] = Field(default_factory=list, max_length=20)
    comments: list[IssueComment] = Field(default_factory=list, max_length=10)


class IssueSummary(ResponseModel):
    number: int | None
    title: str | None = None
    url: str | None = None
    labels: list[str] = Field(default_factory=list)


class PlanData(ResponseModel):
    """Plan contract between the agent and the API/UI."""

    title: str = Field(default="", max_length=100)
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
    gate_messages: list[GateMessage] = Field(default_factory=list)
    gate_revision: int = 0
    gate_chat_busy: bool = False
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
        data["gateChatBusy"] = bool(doc.get("gateChatBusy") and doc.get("gateChatUntil", datetime.min.replace(tzinfo=timezone.utc)) > datetime.now(timezone.utc))
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
