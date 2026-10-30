from datetime import datetime
from typing import Any

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel
from app.schemas.enums import ArtifactType, EventLevel, EventType, TaskStage

ARTIFACT_ID_PATTERN = (
    r"^(plan|repro_test|final_diff|final_tests|attempt_\d{3}_(diff|tests))$"
)


class EventView(ResponseModel):
    seq: int
    ts: datetime
    stage: TaskStage
    type: EventType
    level: EventLevel
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class EventListResponse(ResponseModel):
    items: list[EventView]
    # Pass as `after` to fetch the next page; equals the request's `after` if empty.
    next_after: int


class ArtifactSummary(ResponseModel):
    id: str
    type: ArtifactType
    attempt: int | None = None
    content_type: str
    size_bytes: int
    truncated: bool = False
    sha256: str
    created_at: datetime


class ArtifactView(ArtifactSummary):
    content: str


class ArtifactListResponse(ResponseModel):
    items: list[ArtifactSummary]


class MeResponse(ResponseModel):
    id: str
    github_login: str
    avatar_url: str | None = None
    active_task_id: str | None = None


class RepositoryView(ResponseModel):
    id: str
    full_name: str
    default_branch: str
    private: bool
    language: str | None = None


class RepositoryListResponse(ResponseModel):
    items: list[RepositoryView]


class IssueListItem(ResponseModel):
    number: int
    title: str
    url: str
    labels: list[str] = Field(default_factory=list)


class IssueListResponse(ResponseModel):
    items: list[IssueListItem]


class DevTokenRequest(RequestModel):
    user_id: str = Field(pattern=r"^\d{1,20}$")
    github_login: str = Field(min_length=1, max_length=39)


class TokenResponse(ResponseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
