from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health")
def health(request: Request) -> dict:
    """API liveness plus local worker readiness; no credentials or repository data."""
    settings = request.app.state.settings
    thread = request.app.state.worker_thread
    local_memory = settings.environment in ("local", "test") and settings.store_backend == "memory"
    return {"status": "ok", "worker": {
        "status": ("running" if thread.is_alive() else "stopped") if thread else ("disabled" if local_memory else "external"),
        "agentMode": settings.agent_mode,
    }}
