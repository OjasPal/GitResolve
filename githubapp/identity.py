"""Verified GitHub App bot identity for every commit GitResolve creates.

The identity is never hardcoded into commits. It is resolved from GitHub:
GET /app (App JWT) -> slug -> GET /users/<slug>[bot] -> id -> canonical noreply email.
Anything unexpected fails closed; there is no fallback identity.
"""
import re
from dataclasses import dataclass
from urllib.parse import quote

from githubapp.client import GitHubClientError

# Validation targets only. They are compared against what GitHub returns and are
# never used to build the commit identity (the email uses the API-returned id).
EXPECTED_APP_SLUG = "gitresolve-agent"
EXPECTED_BOT_USER_ID = 351129099

IDENTITY_ERROR = "bot_identity_invalid"
_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$")


@dataclass(frozen=True)
class BotIdentity:
    login: str     # e.g. "gitresolve-agent[bot]"
    user_id: int   # numeric id returned by GET /users/<login>

    @property
    def email(self) -> str:
        return f"{self.user_id}+{self.login}@users.noreply.github.com"

    def git_actor(self, date: str) -> dict:
        """Payload for a Git commit `author` or `committer`."""
        return {"name": "GitResolve Agent", "email": self.email, "date": date}


def resolve_bot_identity(app_client, user_client, app_id, *,
                         expected_slug=EXPECTED_APP_SLUG,
                         expected_user_id=EXPECTED_BOT_USER_ID) -> BotIdentity:
    """Return the verified bot identity or raise GitHubClientError(IDENTITY_ERROR).

    app_client:  client authenticated with the App JWT (needed for GET /app).
    user_client: any authenticated client (installation token) for GET /users/<bot>.
    """
    def invalid():
        return GitHubClientError(IDENTITY_ERROR)

    try:
        app = app_client.request("GET", "/app")
        if str(app.get("id")) != str(app_id):
            raise invalid()
        slug = app.get("slug")
        if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
            raise invalid()
        if expected_slug is not None and slug != expected_slug:
            raise invalid()
        login = f"{slug}[bot]"
        user = user_client.request("GET", "/users/" + quote(login, safe="[]"))
        user_id = user.get("id")
        if user.get("type") != "Bot" or user.get("login") != login:
            raise invalid()
        if type(user_id) is not int or user_id <= 0:
            raise invalid()
        if expected_user_id is not None and user_id != expected_user_id:
            raise invalid()
    except GitHubClientError as exc:
        if exc.code == "github_rate_limited":
            raise
        raise invalid() from None
    except (AttributeError, TypeError, ValueError):
        raise invalid() from None
    return BotIdentity(login, user_id)