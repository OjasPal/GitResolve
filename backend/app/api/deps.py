"""FastAPI dependencies: settings, services, and the authenticated user."""

from fastapi import Depends, Header, Request

from app.core.config import Settings
from app.core.errors import ApiError, unauthorized
from app.core.security import verify_session_token
from app.services.task_service import TaskService


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_task_service(request: Request) -> TaskService:
    return request.app.state.task_service


def current_user_id(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_app_settings),
) -> str:
    """Verified on every request: signature, expiry and `sub` (the user ID)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise unauthorized()
    return verify_session_token(settings, authorization[7:].strip())


def writer_user_id(request: Request, user_id: str = Depends(current_user_id)) -> str:
    """Authenticated user, additionally rate-limited for state-changing actions."""
    retry = request.app.state.user_write_limiter.hit(user_id)
    if retry is not None:
        raise ApiError(
            429,
            "rate_limited",
            "Too many requests. Slow down.",
            {"retryAfterSeconds": int(retry)},
            {"Retry-After": str(int(retry))},
        )
    return user_id
