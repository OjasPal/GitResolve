"""FastAPI application factory. Run: `uvicorn app.main:app --reload`."""

import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, health, repositories, tasks, attachments
from app.core.body_limit import RequestBodyLimitMiddleware
from app.core.config import Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.ratelimit import RateLimitMiddleware, SlidingWindowLimiter
from app.core.security_headers import SecurityHeadersMiddleware
from app.services.github import FakeGitHubGateway, GitHubGateway, GitHubOAuthProvider
from app.services.store import build_store
from app.services.store.base import Store
from app.services.task_service import TaskService, utcnow

API_PREFIX = "/api/v1"


def create_app(
    settings: Settings | None = None,
    store: Store | None = None,
    github: GitHubGateway | None = None,
    clock: Callable[[], datetime] = utcnow,
    github_oauth: GitHubOAuthProvider | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    settings.assert_safe_for_production()
    configure_logging(settings)
    if settings.environment == "production" and settings.github_mode != "real" and (github is None or github_oauth is None):
        raise RuntimeError(
            "Production API requires GitHub repository and OAuth providers."
        )
    store = store or build_store(settings)
    if settings.github_mode == "real":
        from app.services.github_real import RealGitHubGateway, AppOAuthProvider
        github = github or RealGitHubGateway(settings, store)
        github_oauth = github_oauth or AppOAuthProvider(settings)
    elif github is None:
        github = FakeGitHubGateway()

    # A separate worker cannot share an in-memory store. Run it in-process locally.
    local_worker = settings.environment == "local" and settings.store_backend == "memory" and settings.github_mode == "real"

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        stop = threading.Event()
        thread = None
        from app.services.attachments import AttachmentStore
        uploads = AttachmentStore(settings, store)
        def clean_uploads():
            while not stop.is_set():
                uploads.cleanup()
                stop.wait(600)
        cleanup_thread = threading.Thread(target=clean_uploads, name="upload-cleanup", daemon=True)
        cleanup_thread.start()
        if (settings.embedded_worker or local_worker) and settings.environment in ("local", "test"):
            from app.worker.runner import build_worker

            worker = build_worker(settings, store)
            thread = threading.Thread(target=worker.run_forever, args=(stop,), name="embedded-worker", daemon=True)
            thread.start()
            app.state.worker_thread = thread
        try:
            yield
        finally:
            stop.set()
            cleanup_thread.join(timeout=2)
            if thread is not None:
                thread.join(timeout=2)

    app = FastAPI(
        title="GitResolve API",
        version="0.1.0",
        lifespan=lifespan,
        # Interactive docs and the schema are not served in production.
        docs_url="/docs" if settings.docs_on else None,
        redoc_url="/redoc" if settings.docs_on else None,
        openapi_url="/openapi.json" if settings.docs_on else None,
    )
    app.state.worker_thread = None
    app.state.settings = settings
    app.state.store = store
    app.state.github_oauth = github_oauth
    app.state.task_service = TaskService(store, settings, github, clock)
    app.state.user_write_limiter = SlidingWindowLimiter(settings.rate_limit_writes_per_minute_user)

    # Starlette applies the last-added middleware first. Request order is:
    # security headers -> CORS -> rate limit -> body limit -> app, so 413/429
    # responses still carry CORS headers and oversize bodies are never buffered
    # for a client that is already rate limited.
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=settings.max_body_bytes,
    )
    app.add_middleware(
        RateLimitMiddleware,
        limiter=SlidingWindowLimiter(settings.rate_limit_per_minute_ip),
        trusted_hops=settings.trusted_proxy_hops,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,  # explicit list only; never "*"
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts=settings.environment == "production",
        strict_csp=not settings.docs_on,
    )

    install_error_handlers(app)
    app.include_router(health.router)  # /health (liveness, outside the versioned prefix)
    for router in (auth.router, repositories.router, tasks.router, attachments.router):
        app.include_router(router, prefix=API_PREFIX)
    return app


app = create_app()
