"""Process-local Google health and server-directed model cooldowns."""
import time
from collections import deque
from threading import Lock


class ProviderHealth:
    def __init__(self, clock=time.monotonic, window=120, cooldown=60):
        self.clock, self.window, self.cooldown = clock, window, cooldown
        self.failures = deque()
        self.until = 0.0
        self.models = {}
        self.lock = Lock()

    def failed(self, model, retry_after=None):
        with self.lock:
            now = self.clock()
            while self.failures and self.failures[0] < now - self.window:
                self.failures.popleft()
            self.failures.append(now)
            if len(self.failures) >= 2:
                self.until = max(self.until, now + self.cooldown)
            if retry_after is not None:
                self.models[model] = now + max(0, retry_after)

    def blocked(self):
        with self.lock:
            return self.clock() < self.until

    def available(self, model):
        with self.lock:
            return self.clock() >= self.models.get(model, 0)


_health = {}
_lock = Lock()


def shared_health(settings):
    key = (settings.google_cloud_project, settings.google_cloud_location)
    with _lock:
        return _health.setdefault(key, ProviderHealth())
