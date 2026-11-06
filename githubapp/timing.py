"""Secret-free latency instrumentation shared by GitHub and worker stages."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import logging
import time

_records = ContextVar("performance_records", default=None)
_dispatch = ContextVar("performance_model_dispatch", default=None)
_observer = ContextVar("performance_observer", default=None)


@contextmanager
def observe_timings(callback):
    token = _observer.set(callback)
    try:
        yield
    finally:
        _observer.reset(token)


@contextmanager
def model_dispatch_scope(callback):
    token = _dispatch.set(callback)
    try:
        yield
    finally:
        _dispatch.reset(token)


def model_dispatched():
    callback = _dispatch.get()
    if callback is not None:
        callback()

@contextmanager
def measure(name, **fields):
    start = time.perf_counter()
    ok = False
    try:
        yield fields
        ok = True
    finally:
        record = {"operation": name, "duration_ms": round((time.perf_counter() - start) * 1000, 3), "ok": ok, **fields}
        logging.getLogger("gitresolve.performance").info("operation timing", extra=record)
        observer = _observer.get()
        if observer is not None:
            observer(record)
        records = _records.get()
        if records is not None:
            records.append(record)

@contextmanager
def capture_timings():
    records = []
    parent = _records.get()
    token = _records.set(records)
    try:
        yield records
    finally:
        _records.reset(token)
        if parent is not None:
            parent.extend(records)


def timed(name):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            with measure(name):
                return fn(*args, **kwargs)
        return wrapped
    return decorate
