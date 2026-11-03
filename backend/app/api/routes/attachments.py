from fastapi import APIRouter, Depends, Query, Request
from app.api.deps import get_task_service, writer_user_id
from app.services.task_service import TaskService
from app.services.attachments import AttachmentStore
router = APIRouter(prefix="/attachments", tags=["attachments"])
@router.post("", status_code=201)
async def upload_attachment(request: Request, filename: str = Query(min_length=1, max_length=120), user_id: str = Depends(writer_user_id), service: TaskService = Depends(get_task_service)):
    import asyncio
    data = await request.body()
    uploads = AttachmentStore(service.settings, service.store)
    return await asyncio.to_thread(uploads.upload, user_id, filename, request.headers.get("content-type", ""), data)
