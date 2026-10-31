import threading

from app.schemas.enums import EventType
from app.services.transitions import EventSpec, build_event


def test_worker_loop_recovers_after_store_outage(env, monkeypatch):
    stop = threading.Event()
    calls = []
    env.settings.poll_interval_seconds = 0
    def run_once():
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("temporary store outage")
        stop.set()
        return False
    monkeypatch.setattr(env.worker, "run_once", run_once)
    env.worker.run_forever(stop)
    assert len(calls) == 2


def test_event_metadata_drops_nonfinite_values(env):
    event = build_event({"createdAt": env.clock()}, 1, "test",
                        EventSpec(EventType.INFO, "progress", data={"execSeconds": float("nan"), "ran": 3}),
                        env.clock(), env.settings)
    assert event["data"] == {"ran": 3}
