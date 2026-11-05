"""Bot identity verification: App id, slug, Bot type, canonical email, fail-closed."""
import pytest

from githubapp.client import GitHubClientError
from githubapp.identity import BotIdentity, IDENTITY_ERROR, resolve_bot_identity

LOGIN = "gitresolve-agent[bot]"
BOT_PATH = "/users/gitresolve-agent[bot]"
EMAIL = "351129099+gitresolve-agent[bot]@users.noreply.github.com"


class FakeClient:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def request(self, method, path, **kwargs):
        self.calls.append((method, path))
        value = self.routes[path]
        if isinstance(value, Exception):
            raise value
        return value


def clients(app=None, user=None):
    app = {"id": 12345, "slug": "gitresolve-agent"} if app is None else app
    user = {"login": LOGIN, "id": 351129099, "type": "Bot"} if user is None else user
    return FakeClient({"/app": app}), FakeClient({BOT_PATH: user})


def test_valid_identity_and_canonical_email():
    app_client, user_client = clients()
    identity = resolve_bot_identity(app_client, user_client, "12345")
    assert identity == BotIdentity(LOGIN, 351129099)
    assert identity.email == EMAIL
    assert app_client.calls == [("GET", "/app")]
    assert user_client.calls == [("GET", BOT_PATH)]


def test_app_id_accepts_int_or_str_but_rejects_mismatch():
    app_client, user_client = clients()
    assert resolve_bot_identity(app_client, user_client, 12345).login == LOGIN
    app_client, user_client = clients()
    with pytest.raises(GitHubClientError) as exc:
        resolve_bot_identity(app_client, user_client, "999")
    assert exc.value.code == IDENTITY_ERROR and user_client.calls == []


@pytest.mark.parametrize("slug", ["gitresolve", "someone-else", "", None, "Bad Slug"])
def test_wrong_app_slug_fails_before_user_lookup(slug):
    app_client, user_client = clients(app={"id": 12345, "slug": slug})
    with pytest.raises(GitHubClientError) as exc:
        resolve_bot_identity(app_client, user_client, 12345)
    assert exc.value.code == IDENTITY_ERROR and user_client.calls == []


def test_unrelated_user_account_is_rejected():
    # The old bug: a normal account named "gitresolve" must never be accepted.
    app_client, user_client = clients(user={"login": "gitresolve", "id": 1, "type": "User"})
    with pytest.raises(GitHubClientError):
        resolve_bot_identity(app_client, user_client, 12345)


@pytest.mark.parametrize("user", [
    {"login": LOGIN, "id": 351129099, "type": "User"},
    {"login": "other[bot]", "id": 351129099, "type": "Bot"},
    {"login": "GitResolve-Agent[bot]", "id": 351129099, "type": "Bot"},
    {"login": LOGIN, "id": 111, "type": "Bot"},
    {"login": LOGIN, "id": "351129099", "type": "Bot"},
    {"login": LOGIN, "id": True, "type": "Bot"},
    {"login": LOGIN, "type": "Bot"},
])
def test_wrong_bot_account_fails(user):
    app_client, user_client = clients(user=user)
    with pytest.raises(GitHubClientError) as exc:
        resolve_bot_identity(app_client, user_client, 12345)
    assert exc.value.code == IDENTITY_ERROR


def test_email_uses_api_returned_id_not_a_constant():
    app_client, user_client = clients(user={"login": LOGIN, "id": 42, "type": "Bot"})
    identity = resolve_bot_identity(app_client, user_client, 12345, expected_user_id=None)
    assert identity.email == "42+gitresolve-agent[bot]@users.noreply.github.com"


def test_github_errors_fail_closed_and_rate_limit_is_preserved():
    app_client = FakeClient({"/app": GitHubClientError("access_revoked")})
    with pytest.raises(GitHubClientError) as exc:
        resolve_bot_identity(app_client, FakeClient({}), 12345)
    assert exc.value.code == IDENTITY_ERROR
    app_client, user_client = clients()
    user_client.routes[BOT_PATH] = GitHubClientError("access_revoked")  # 404 on /users
    with pytest.raises(GitHubClientError) as exc:
        resolve_bot_identity(app_client, user_client, 12345)
    assert exc.value.code == IDENTITY_ERROR
    app_client = FakeClient({"/app": GitHubClientError("github_rate_limited")})
    with pytest.raises(GitHubClientError) as exc:
        resolve_bot_identity(app_client, FakeClient({}), 12345)
    assert exc.value.code == "github_rate_limited"


def test_git_actor_payload_has_name_email_date():
    actor = BotIdentity(LOGIN, 351129099).git_actor("2026-01-01T00:00:00")
    assert actor == {"name": "GitResolve Agent", "email": EMAIL, "date": "2026-01-01T00:00:00"}
