"""Bounded process-local cache. Never use cached listings to authorize writes."""
from copy import deepcopy
from threading import RLock
import time


class TTLCache:
    def __init__(self, ttl=15, capacity=128, clock=time.monotonic):
        self.ttl, self.capacity, self.clock = ttl, capacity, clock
        self._entries = {}
        self._lock = RLock()

    def get(self, key):
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry[0] <= self.clock():
                self._entries.pop(key, None)
                return None
            return deepcopy(entry[1])

    def put(self, key, value):
        with self._lock:
            if len(self._entries) >= self.capacity:
                self._entries.pop(next(iter(self._entries)))
            self._entries[key] = (self.clock() + self.ttl, deepcopy(value))

    def clear(self):
        with self._lock:
            self._entries.clear()
