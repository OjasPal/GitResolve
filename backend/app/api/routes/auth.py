import hmac
import secrets

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse

from app.api.deps import current_user_id, get_app_settings, get_task_service
from app.core.config import Settings
from app.core.errors import ApiError, unauthorized
from app.core.security import create_session_token
from app.schemas.resources import DevTokenRequest, MeResponse, TokenResponse
from app.services.github import GitHubOAuthProvider, GitHubUnavailable
from app.services.task_service import TaskService

router = APIRouter(tags=["auth"])
OAUTH_STATE_COOKIE = "gitresolve_oauth_state"


def _oauth_redirect(
    settings: Settings,
    *,
    token: str | None = None,
    clear_state: bool = False,
) -> RedirectResponse:
    callback = f"{settings.frontend_base_url.rstrip('/')}/auth/callback"
    if token:
        redirect = RedirectResponse(f"{callback}#accessToken={token}", status_code=302)
    else:
        redirect = RedirectResponse(f"{callback}#error=github_auth_failed", status_code=302)
    if clear_state:
        redirect.delete_cookie(OAUTH_STATE_COOKIE, path="/api/v1/auth/github/callback")
    return redirect


def _configured_github_oauth(request: Request) -> GitHubOAuthProvider:
    provider = request.app.state.github_oauth
    if provider is None:
        raise ApiError(404, "not_found", "GitHub sign-in is not configured.")
    return provider


@router.get("/auth/github/login", include_in_schema=True)
def github_login(
    settings: Settings = Depends(get_app_settings),
    github: GitHubOAuthProvider = Depends(_configured_github_oauth),
) -> RedirectResponse:
    state = secrets.token_urlsafe(32)
    redirect = RedirectResponse(
        github.authorization_url(state, settings.github_app_callback_url),
        status_code=302,
    )
    redirect.set_cookie(
        OAUTH_STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        secure=settings.environment == "production",
        samesite="lax",
        path="/api/v1/auth/github/callback",
    )
    return redirect


@router.get("/auth/github/callback", include_in_schema=True)
def github_callback(
    request: Request,
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    settings: Settings = Depends(get_app_settings),
    service: TaskService = Depends(get_task_service),
    github: GitHubOAuthProvider = Depends(_configured_github_oauth),
) -> RedirectResponse:
    cookie_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if error or not code or not state or not cookie_state or not hmac.compare_digest(state, cookie_state):
        return _oauth_redirect(settings, clear_state=True)

    try:
        identity = github.authenticate(code, settings.github_app_callback_url)
    except GitHubUnavailable:
        return _oauth_redirect(settings, clear_state=True)

    service.upsert_user(
        identity.user_id,
        identity.login,
        identity.avatar_url,
        identity.installation_ids,
    )
    token, _ = create_session_token(settings, identity.user_id)
    return _oauth_redirect(settings, token=token, clear_state=True)


@router.post("/auth/dev-token", response_model=TokenResponse, include_in_schema=True)
def dev_token(
    body: DevTokenRequest,
    settings: Settings = Depends(get_app_settings),
    service: TaskService = Depends(get_task_service),
) -> TokenResponse:
    """LOCAL/TEST ONLY: mint a session JWT without GitHub OAuth.

    Returns 404 outside `ENVIRONMENT=local|test`. Replaced by the GitHub OAuth
    callback (`/auth/github/*`) in Phase 3.
    """
    if settings.environment not in ("local", "test"):
        raise ApiError(404, "not_found", "Not found.")
    service.upsert_user(body.user_id, body.github_login)
    token, expires = create_session_token(settings, body.user_id)
    return TokenResponse(access_token=token, expires_at=expires)


@router.get("/me", response_model=MeResponse)
def me(
    user_id: str = Depends(current_user_id),
    service: TaskService = Depends(get_task_service),
) -> MeResponse:
    user = service.get_user(user_id)
    if user is None:
        raise unauthorized("Unknown user.")
    return MeResponse(
        id=user_id,
        github_login=user["githubLogin"],
        avatar_url=user.get("avatarUrl"),
        active_task_id=user.get("activeTaskId"),
    )
