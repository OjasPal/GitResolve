import pytest

from app.main import create_app
from app.services.github import FakeGitHubGateway, GitHubIdentity
from app.services.store.memory import MemoryStore
from fastapi.testclient import TestClient

from app.core.config import Settings
from tests.conftest import API, build_env

ORIGIN = {"Origin": "http://localhost:3000"}


def production_settings(**overrides) -> Settings:
    values = dict(
        environment="production",
        store_backend="firestore",
        session_jwt_secret="a-strong-session-secret-with-more-than-32-bytes",
        gcp_project="gitresolve-prod",
        github_app_id=123,
        github_app_client_id="client-id",
        github_app_client_secret="client-secret",
        github_app_private_key="private-key",
        github_app_callback_url="https://api.example.test/api/v1/auth/github/callback",
        frontend_base_url="https://gitresolve.vercel.app",
        cors_origins=["https://gitresolve.vercel.app"],
        _env_file=None,
    )
    values.update(overrides)
    return Settings(**values)


class OAuthProvider:
    def authorization_url(self, state: str, redirect_uri: str) -> str:
        return f"https://github.com/login/oauth/authorize?state={state}"

    def authenticate(self, code: str, redirect_uri: str) -> GitHubIdentity:
        return GitHubIdentity("1", "test-user", None, [])


def production_client(**overrides) -> TestClient:
    # Providers are injected so nothing external is contacted.
    app = create_app(
        production_settings(**overrides),
        store=MemoryStore(),
        github=FakeGitHubGateway(),
        github_oauth=OAuthProvider(),
    )
    return TestClient(app)


def test_security_headers_on_every_response(env):
    r = env.client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in r.headers  # HSTS only in production
    assert "content-security-policy" not in r.headers  # local docs page needs scripts
    assert env.client.get(f"{API}/tasks").headers["x-content-type-options"] == "nosniff"  # also on errors


def test_production_hides_docs_and_adds_hsts_and_csp():
    client = production_client()
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404
    r = client.get("/health")
    assert r.status_code == 200
    assert "max-age=31536000" in r.headers["strict-transport-security"]
    assert r.headers["content-security-policy"].startswith("default-src 'none'")


def test_production_requires_member_three_github_providers():
    with pytest.raises(RuntimeError, match="GitHub repository and OAuth providers"):
        create_app(
            production_settings(),
            store=MemoryStore(),
            github=FakeGitHubGateway(),
        )


def test_production_dev_token_is_unavailable_and_docs_can_be_re_enabled():
    client = production_client(docs_enabled=True)
    assert client.get("/openapi.json").status_code == 200
    r = client.post(f"{API}/auth/dev-token", json={"userId": "1", "githubLogin": "x"})
    assert r.status_code == 404


def test_per_ip_limit_returns_429_with_retry_after_and_cors_headers():
    env = build_env(rate_limit_per_minute_ip=4)
    for _ in range(4):
        assert env.client.get(f"{API}/tasks").status_code == 401
    r = env.client.get(f"{API}/tasks", headers=ORIGIN)
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert int(r.headers["retry-after"]) >= 1
    assert r.headers["access-control-allow-origin"] == ORIGIN["Origin"]  # browser can read the error
    assert env.client.get("/health").status_code == 200  # health is exempt
    preflight = env.client.options(
        f"{API}/tasks", headers={**ORIGIN, "Access-Control-Request-Method": "POST"}
    )
    assert preflight.status_code == 200  # preflight requests are not rate limited


def test_forwarded_for_spoofing_cannot_evade_the_ip_limit():
    env = build_env(rate_limit_per_minute_ip=3, trusted_proxy_hops=1)
    for i in range(3):
        r = env.client.get(f"{API}/tasks", headers={"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.7"})
        assert r.status_code == 401
    spoofed = env.client.get(f"{API}/tasks", headers={"X-Forwarded-For": "8.8.8.8, 203.0.113.7"})
    assert spoofed.status_code == 429
    other = env.client.get(f"{API}/tasks", headers={"X-Forwarded-For": "8.8.8.8, 198.51.100.9"})
    assert other.status_code == 401  # a different real client has its own budget


def test_per_user_write_limit_covers_actions_not_reads_or_other_users():
    env = build_env(rate_limit_writes_per_minute_user=2)
    h = env.headers()
    first = env.create(h).json()["id"]
    assert env.post(h, first, "cancel").status_code == 200
    r = env.create(h)
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert r.json()["error"]["details"]["retryAfterSeconds"] >= 1 and "retry-after" in r.headers
    assert env.client.get(f"{API}/tasks", headers=h).status_code == 200
    assert env.create(env.headers("2002")).status_code == 202


def test_oversized_body_is_rejected_with_cors_headers(env):
    r = env.client.post(
        f"{API}/tasks",
        headers={**env.headers(), **ORIGIN},
        content=b"x" * (env.settings.max_body_bytes + 1),
    )
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"
    assert r.headers["access-control-allow-origin"] == ORIGIN["Origin"]


def test_cors_rejects_unlisted_origins(env):
    r = env.client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers
