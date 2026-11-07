"""Process-local Google GenAI per-model circuit breakers and server-directed cooldowns (Phase 3).

Breaker/health state is environmental and process-local: it is NEVER serialized into LangGraph checkpoints.
"""
from __future__ import annotations

from collections import defaultdict, deque
from threading import Lock
import time
from typing import Callable


class ProviderHealth:
    """Per-model circuit breaker and cooldown tracker.

    - Each model (`gemini-3.8-flash`, `gemini-3.6-flash`, `gemini-4-argon`) has its own failure window
      and cooldown timer (`self.models[model]`).
    - One model failing (or having an open breaker) does NOT block the other models, including the specialist.
    - An open specialist breaker (`gemini-4-argon`) NEVER affects the primary/fallback chain or the fast lane.
    - `blocked()` is True only when multiple non-specialist models in the Flash chain have failed within the window.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        window: float = 120.0,
        cooldown: float = 60.0,
        breaker_threshold: int = 2,
    ) -> None:
        self.clock = clock
        self.window = window
        self.cooldown = cooldown
        self.breaker_threshold = max(1, breaker_threshold)
        self.failures: deque[tuple[float, str]] = deque()
        self.model_failures: dict[str, deque[float]] = defaultdict(deque)
        self.until: float = 0.0
        self.models: dict[str, float] = {}
        self.lock = Lock()

    @staticmethod
    def _is_specialist_model(model: str) -> bool:
        return "argon" in (model or "").lower() or "specialist" in (model or "").lower()

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self.window
        while self.failures and self.failures[0][0] < cutoff:
            self.failures.popleft()
        for m_deque in self.model_failures.values():
            while m_deque and m_deque[0] < cutoff:
                m_deque.popleft()

    def failed(self, model: str, retry_after: float | None = None) -> None:
        with self.lock:
            now = self.clock()
            self._prune_locked(now)
            m_deque = self.model_failures[model]
            m_deque.append(now)

            # Open per-model breaker if this model reached breaker_threshold failures in window
            if len(m_deque) >= self.breaker_threshold:
                self.models[model] = max(self.models.get(model, 0.0), now + self.cooldown)

            if retry_after is not None:
                self.models[model] = max(self.models.get(model, 0.0), now + max(0.0, float(retry_after)))

            # Specialist failures never trip the Flash chain circuit breaker
            if not self._is_specialist_model(model):
                self.failures.append((now, model))
                distinct_flash_models = {m for _, m in self.failures}
                if len(distinct_flash_models) >= 2:
                    self.until = max(self.until, now + self.cooldown)

    def record_success(self, model: str) -> None:
        """Clear sliding-window failure history for a model after a healthy response."""
        with self.lock:
            self.model_failures.pop(model, None)
            if self.models.get(model, 0.0) <= self.clock():
                self.models.pop(model, None)

    def open_breaker(self, model: str, duration: float | None = None) -> None:
        """Explicitly open the circuit breaker for a single model without affecting other models."""
        with self.lock:
            now = self.clock()
            self.models[model] = max(self.models.get(model, 0.0), now + (duration if duration is not None else self.cooldown))

    def blocked(self) -> bool:
        """Return True only when the non-specialist Flash chain circuit is globally open."""
        with self.lock:
            return self.clock() < self.until

    def available(self, model: str) -> bool:
        """Return True if this specific model's circuit breaker / cooldown is not open."""
        with self.lock:
            return self.clock() >= self.models.get(model, 0.0)

    def model_has_recent_failures(self, model: str) -> bool:
        with self.lock:
            now = self.clock()
            self._prune_locked(now)
            return len(self.model_failures.get(model, ())) > 0 or now < self.models.get(model, 0.0)


_health: dict[tuple[str, str], ProviderHealth] = {}
_lock = Lock()


def shared_health(settings: object) -> ProviderHealth:
    key = (
        getattr(settings, "google_cloud_project", ""),
        getattr(settings, "google_cloud_location", "global"),
    )
    with _lock:
        return _health.setdefault(key, ProviderHealth())
