"""Sliding-window rate limiting (TRD section 5: per-IP and per-user limits).

In-process, so each Cloud Run instance counts separately; with at most two
instances the effective limit is at most doubled. Revisit before scaling out.
"""

import threading
import time
from collections import deque
from collections.abc import Callable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> float | None:
        """Record a request. Returns seconds to wait when over the limit, else None."""
        if self.limit <= 0:
            return None
        now = self._clock()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return max(1.0, q[0] + self.window - now)
            q.append(now)
            if len(self._hits) > 10_000:  # bound memory under key churn
                for k in [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window]:
                    del self._hits[k]
            return None


def client_ip(scope: Scope, trusted_hops: int) -> str:
    """Client address. Behind N trusted proxies, take the Nth entry from the right of
    X-Forwarded-For; entries further left are client-controlled and ignored."""
    if trusted_hops > 0:
        for key, value in scope.get("headers", []):
            if key == b"x-forwarded-for":
                parts = [p.strip() for p in value.decode("latin-1").split(",") if p.strip()]
                if len(parts) >= trusted_hops:
                    return parts[-trusted_hops]
                break
    client = scope.get("client")
    return client[0] if client else "unknown"


class RateLimitMiddleware:
    """Per-IP limit for every HTTP request except `/health`."""

    def __init__(self, app: ASGIApp, limiter: SlidingWindowLimiter, trusted_hops: int = 0):
        self.app = app
        self.limiter = limiter
        self.trusted_hops = trusted_hops

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == "/health" or scope["method"] == "OPTIONS":
            await self.app(scope, receive, send)
            return
        retry = self.limiter.hit(client_ip(scope, self.trusted_hops))
        if retry is None:
            await self.app(scope, receive, send)
            return
        response = JSONResponse(
            {"error": {"code": "rate_limited", "message": "Too many requests. Slow down."}},
            status_code=429,
            headers={"Retry-After": str(int(retry))},
        )
        await response(scope, receive, send)
