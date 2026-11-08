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
    page: int = Query(1, ge=1, le=1000),
    user_id: str = Depends(current_user_id),
    service: TaskService = Depends(get_task_service),
) -> RepositoryListResponse:
    gateway = service.github
    if hasattr(gateway, 'repository_page'):
        repos, next_page = service._github(lambda: gateway.repository_page(user_id, page))
    else:
        repos, next_page = (service.list_repositories(user_id) if page == 1 else []), None
    return RepositoryListResponse(
        next_page=next_page,
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
    page: int = Query(1, ge=1, le=1000),
    user_id: str = Depends(current_user_id),
    service: TaskService = Depends(get_task_service),
) -> IssueListResponse:
    gateway = service.github
    if hasattr(gateway, 'issue_page'):
        issues, next_page = service._github(lambda: gateway.issue_page(user_id, repo_id, page))
    else:
        issues, next_page = (service.list_issues(user_id, repo_id) if page == 1 else []), None
    return IssueListResponse(
        next_page=next_page,
        items=[IssueListItem(number=i.number, title=i.title, url=i.url, labels=i.labels) for i in issues]
    )
