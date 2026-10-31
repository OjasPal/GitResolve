from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.agent.fake import FakeAgentRunner
from app.core.config import Settings
from app.main import create_app
from app.services.github import FakeGitHubGateway
from app.services.store.memory import MemoryStore
from app.services.worker_service import WorkerService
from app.worker.pr import FakePullRequestCreator
from app.worker.runner import Worker

API = "/api/v1"
REPO_ID = FakeGitHubGateway.REPO.repo_id


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kwargs: float) -> None:
        self.t += timedelta(**kwargs)


@dataclass
class Env:
    settings: Settings
    store: MemoryStore
    github: FakeGitHubGateway
    clock: Clock
    client: TestClient
    app: object
    agent: FakeAgentRunner
    pr_creator: FakePullRequestCreator
    worker_service: WorkerService
    worker: Worker

    def headers(self, user_id: str = "1001") -> dict[str, str]:
        r = self.client.post(f"{API}/auth/dev-token", json={"userId": user_id, "githubLogin": f"user{user_id}"})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['accessToken']}"}

    def create(self, headers, issue: int = 1, key: str | None = None, repo: str = REPO_ID):
        h = dict(headers)
        if key:
            h["Idempotency-Key"] = key
        return self.client.post(f"{API}/tasks", json={"repoId": repo, "issueNumber": issue}, headers=h)

    def get(self, headers, task_id: str) -> dict:
        r = self.client.get(f"{API}/tasks/{task_id}", headers=headers)
        assert r.status_code == 200, r.text
        return r.json()

    def post(self, headers, task_id: str, action: str, body: dict | None = None):
        return self.client.post(f"{API}/tasks/{task_id}/{action}", headers=headers, json=body)

    def run_worker(self) -> bool:
        return self.worker.run_once()


def build_env(**settings_overrides) -> Env:
    """Build a fully wired in-memory environment; keyword args override Settings fields."""
    settings = Settings(environment="test", _env_file=None, **settings_overrides)
    store, github, clock = MemoryStore(), FakeGitHubGateway(), Clock()
    app = create_app(settings=settings, store=store, github=github, clock=clock)
    agent, pr = FakeAgentRunner(), FakePullRequestCreator()
    svc = WorkerService(store, settings, clock)
    worker = Worker(svc, agent, pr, settings, worker_id="w1")
    return Env(settings, store, github, clock, TestClient(app), app, agent, pr, svc, worker)


@pytest.fixture
def env() -> Env:
    return build_env()
