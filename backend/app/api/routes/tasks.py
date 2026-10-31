from fastapi import APIRouter, Depends, Header, Path, Query, Response

from app.api.deps import current_user_id, get_task_service, writer_user_id
from app.schemas.common import ErrorResponse
from app.schemas.resources import (
    ARTIFACT_ID_PATTERN,
    ArtifactListResponse,
    ArtifactSummary,
    ArtifactView,
    EventListResponse,
    EventView,
)
from app.schemas.tasks import (
    ApproveRequest,
    CreateTaskRequest,
    TaskListResponse,
    TaskResponse,
    TaskSummary,
)
from app.services.task_service import TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])

TASK_ID = Path(pattern=r"^tsk_[0-9a-f]{20}$")
ERRORS = {
    401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
}


@router.post("", status_code=202, response_model=TaskResponse, responses={**ERRORS, 200: {"model": TaskResponse}, 429: {"model": ErrorResponse}, 502: {"model": ErrorResponse}})
def create_task(
    body: CreateTaskRequest,
    response: Response,
    idempotency_key: str | None = Header(default=None, min_length=1, max_length=128),
    user_id: str = Depends(writer_user_id),
    service: TaskService = Depends(get_task_service),
) -> TaskResponse:
    """Queue a task and return immediately (202). Planning runs in the worker.

    A repeated `Idempotency-Key` with the same body returns the existing task (200).
    """
    task, created = service.create_task(user_id, body.repo_id, body.issue_number, idempotency_key)
    if not created:
        response.status_code = 200
    return TaskResponse.from_doc(task)


@router.get("", response_model=TaskListResponse, responses=ERRORS)
def list_tasks(
    limit: int = Query(20, ge=1, le=50),
    cursor: str | None = Query(None, pattern=r"^tsk_[0-9a-f]{20}$"),
    user_id: str = Depends(current_user_id),
    service: TaskService = Depends(get_task_service),
) -> TaskListResponse:
    rows, next_cursor = service.list_tasks(user_id, limit, cursor)
    return TaskListResponse(items=[TaskSummary.from_doc(r) for r in rows], next_cursor=next_cursor)


@router.get("/{task_id}", response_model=TaskResponse, responses=ERRORS)
def get_task(
    task_id: str = TASK_ID,
    user_id: str = Depends(current_user_id),
    service: TaskService = Depends(get_task_service),
) -> TaskResponse:
    return TaskResponse.from_doc(service.get_task(user_id, task_id))


@router.post("/{task_id}/start", response_model=TaskResponse, responses=ERRORS)
def start_task(
    task_id: str = TASK_ID,
    user_id: str = Depends(writer_user_id),
    service: TaskService = Depends(get_task_service),
) -> TaskResponse:
    """Human gate 1: start repair after reviewing the plan (`awaiting_start` -> `running`)."""
    return TaskResponse.from_doc(service.start_task(user_id, task_id))


@router.post("/{task_id}/cancel", response_model=TaskResponse, responses=ERRORS)
def cancel_task(
    response: Response,
    task_id: str = TASK_ID,
    user_id: str = Depends(writer_user_id),
    service: TaskService = Depends(get_task_service),
) -> TaskResponse:
    """200 when cancelled immediately; 202 when requested and the worker will stop at its next checkpoint."""
    task, cancelled_now = service.cancel_task(user_id, task_id)
    response.status_code = 200 if cancelled_now else 202
    return TaskResponse.from_doc(task)


@router.post("/{task_id}/approve", response_model=TaskResponse, responses=ERRORS)
def approve_task(
    body: ApproveRequest,
    task_id: str = TASK_ID,
    user_id: str = Depends(writer_user_id),
    service: TaskService = Depends(get_task_service),
) -> TaskResponse:
    """Human gate 2: approve the exact diff hash. Also retries PR creation after a PR-phase failure."""
    return TaskResponse.from_doc(service.approve_task(user_id, task_id, body.diff_hash))


@router.post("/{task_id}/reject", response_model=TaskResponse, responses=ERRORS)
def reject_task(
    task_id: str = TASK_ID,
    user_id: str = Depends(writer_user_id),
    service: TaskService = Depends(get_task_service),
) -> TaskResponse:
    return TaskResponse.from_doc(service.reject_task(user_id, task_id))


@router.get("/{task_id}/events", response_model=EventListResponse, responses=ERRORS)
def list_events(
    task_id: str = TASK_ID,
    after: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=50),
    user_id: str = Depends(current_user_id),
    service: TaskService = Depends(get_task_service),
) -> EventListResponse:
    """Events with `seq > after`, oldest first. Poll with `after=nextAfter`."""
    rows, next_after = service.list_events(user_id, task_id, after, limit)
    return EventListResponse(items=[EventView.model_validate(r) for r in rows], next_after=next_after)


@router.get("/{task_id}/artifacts", response_model=ArtifactListResponse, responses=ERRORS)
def list_artifacts(
    task_id: str = TASK_ID,
    user_id: str = Depends(current_user_id),
    service: TaskService = Depends(get_task_service),
) -> ArtifactListResponse:
    rows = service.list_artifacts(user_id, task_id)
    return ArtifactListResponse(items=[ArtifactSummary.model_validate(r) for r in rows])


@router.get("/{task_id}/artifacts/{artifact_id}", response_model=ArtifactView, responses=ERRORS)
def get_artifact(
    task_id: str = TASK_ID,
    artifact_id: str = Path(pattern=ARTIFACT_ID_PATTERN),
    user_id: str = Depends(current_user_id),
    service: TaskService = Depends(get_task_service),
) -> ArtifactView:
    return ArtifactView.model_validate(service.get_artifact(user_id, task_id, artifact_id))
