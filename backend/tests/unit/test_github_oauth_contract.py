from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.security import verify_session_token
from app.main import create_app
from app.services.github import GitHubIdentity
from app.services.store.memory import MemoryStore


class OAuthProvider:
    def __init__(self):
        self.authenticated_codes = []
        self.callback_urls = []

    def authorization_url(self, state: str, redirect_uri: str) -> str:
        return (
            "https://github.com/login/oauth/authorize"
            f"?state={state}&redirect_uri={redirect_uri}"
        )

    def authenticate(self, code: str, redirect_uri: str) -> GitHubIdentity:
        self.authenticated_codes.append(code)
        self.callback_urls.append(redirect_uri)
        return GitHubIdentity(
            user_id="42",
            login="octocat",
            avatar_url="https://example.test/avatar",
            installation_ids=[1001],
        )


def test_auth_routes_validate_oauth_state_and_delegate_github_details():
    settings = Settings(environment="test", _env_file=None)
    store = MemoryStore()
    provider = OAuthProvider()
    app = create_app(settings=settings, store=store, github_oauth=provider)

    with TestClient(app) as client:
        login = client.get("/api/v1/auth/github/login", follow_redirects=False)
        assert login.status_code == 302
        params = parse_qs(urlparse(login.headers["location"]).query)
        state = params["state"][0]

        rejected = client.get(
            "/api/v1/auth/github/callback",
            params={"code": "temporary-code", "state": "wrong-state"},
            follow_redirects=False,
        )
        assert rejected.headers["location"].endswith("#error=github_auth_failed")
        assert provider.authenticated_codes == []
        assert store.get("users/42") is None

        login = client.get("/api/v1/auth/github/login", follow_redirects=False)
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        callback = client.get(
            "/api/v1/auth/github/callback",
            params={"code": "temporary-code", "state": state},
            follow_redirects=False,
        )

        token = parse_qs(urlparse(callback.headers["location"]).fragment)["accessToken"][0]
        assert verify_session_token(settings, token) == "42"
        assert provider.authenticated_codes == ["temporary-code"]
        user = store.get("users/42")
        assert user["githubLogin"] == "octocat"
        assert user["installationIds"] == [1001]


@pytest.mark.parametrize("origin", ["http://localhost:8000", "https://shared.example.test"])
def test_local_and_shared_oauth_keep_callback_on_the_starting_host(origin):
    path = "/api/v1/auth/github/callback"
    settings = Settings(
        environment="local", _env_file=None,
        github_app_callback_url="https://shared.example.test" + path,
        github_app_local_callback_url="http://localhost:8000" + path,
    )
    provider = OAuthProvider()
    app = create_app(settings=settings, store=MemoryStore(), github_oauth=provider)
    with TestClient(app, base_url=origin) as client:
        login = client.get("/api/v1/auth/github/login", follow_redirects=False)
        params = parse_qs(urlparse(login.headers["location"]).query)
        assert params["redirect_uri"] == [origin + path]
        result = client.get(path, params={"code": "test-code", "state": params["state"][0]},
                            follow_redirects=False)
        assert "#accessToken=" in result.headers["location"]
        assert provider.callback_urls == [origin + path]
