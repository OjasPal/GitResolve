from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import current_user_id, get_task_service
from app.schemas.resources import (
    IssueListItem,
    IssueListResponse,
    RepositoryListResponse,
    RepositoryView,
)
from app.schemas.tasks import REPO_ID_PATTERN
from app.services.task_service import TaskService

router = APIRouter(tags=["repositories"])


@router.get("/repositories", response_model=RepositoryListResponse)
def list_repositories(
    user_id: str = Depends(current_user_id),
    service: TaskService = Depends(get_task_service),
) -> RepositoryListResponse:
    repos = service.list_repositories(user_id)
    return RepositoryListResponse(
        items=[
            RepositoryView(
                id=r.repo_id, full_name=r.full_name, default_branch=r.default_branch,
                private=r.private, language=r.language,
            )
            for r in repos
        ]
    )


@router.get("/repositories/{repo_id}/issues", response_model=IssueListResponse)
def list_issues(
    repo_id: str = Path(pattern=REPO_ID_PATTERN),
    state: str = Query("open", pattern="^open$"),
    user_id: str = Depends(current_user_id),
    service: TaskService = Depends(get_task_service),
) -> IssueListResponse:
    issues = service.list_issues(user_id, repo_id)
    return IssueListResponse(
        items=[IssueListItem(number=i.number, title=i.title, url=i.url, labels=i.labels) for i in issues]
    )
