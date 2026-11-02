import threading

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.github import FakeGitHubGateway
from app.services.store.memory import MemoryStore


def test_local_real_memory_worker_shares_api_store(monkeypatch):
    started = threading.Event()
    store = MemoryStore()
    settings = Settings(_env_file=None, environment="local", github_mode="real", agent_mode="langgraph",
                        github_app_id=1, github_app_private_key="test-only", github_app_client_id="id",
                        github_app_client_secret="secret", session_jwt_secret="a" * 40)

    class Worker:
        def run_forever(self, stop):
            started.set()
            stop.wait(5)

    def build(s, shared_store):
        assert s is settings and shared_store is store
        return Worker()

    monkeypatch.setattr("app.worker.runner.build_worker", build)
    app = create_app(settings=settings, store=store, github=FakeGitHubGateway(), github_oauth=object())
    with TestClient(app) as client:
        assert started.wait(1)
        health = client.get("/health").json()
        assert health["worker"] == {"status": "running", "agentMode": "langgraph"}
    assert not app.state.worker_thread.is_alive()
