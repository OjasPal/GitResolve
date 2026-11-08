"""GitHub App authentication. Tokens live in memory only: never log or persist them."""

from githubapp.timing import timed

import time
from datetime import datetime
from threading import RLock

import jwt  # PyJWT[crypto]


class GitHubAuth:
    def __init__(self, app_id: str, private_key_pem: str):
        if not app_id or not private_key_pem:
            raise ValueError("app_id and private_key_pem are required")
        self.app_id = app_id
        self._private_key = private_key_pem
        self._tokens = {}
        self._token_lock = RLock()

    def _generate_jwt(self) -> str:
        now = int(time.time())
        payload = {"iat": now - 60, "exp": now + 9 * 60, "iss": self.app_id}
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    @timed("github.token")
    def get_installation_token(
        self,
        installation_id: int,
        repositories: list[str] | None = None,
        permissions: dict[str, str] | None = None,
        force_refresh: bool = False,
    ) -> str:
        """Short-lived (~1h) token. Pass repositories/permissions to narrow it further."""
        key = (int(installation_id), tuple(sorted(repositories or [])), tuple(sorted((permissions or {}).items())))
        with self._token_lock:
            if force_refresh:
                self._tokens.pop(key, None)
            cached = self._tokens.get(key)
            if cached and cached[0] > time.time():
                return cached[1]
            token, expires = self._mint(installation_id, repositories, permissions)
            if len(self._tokens) >= 128:
                self._tokens.clear()
            self._tokens[key] = (expires, token)
            return token

    def _mint(self, installation_id, repositories, permissions):
        body: dict = {}
        if repositories:
            body["repositories"] = repositories
        if permissions:
            body["permissions"] = permissions
        from githubapp.client import GitHubClient, GitHubClientError
        try:
            client = GitHubClient(self._generate_jwt())
            result = client.request("POST", f"/app/installations/{int(installation_id)}/access_tokens",
                                    json=body or None)
            expires = datetime.fromisoformat(result["expires_at"].replace("Z", "+00:00")).timestamp() - 60 if result.get("expires_at") else time.time()
            return result["token"], min(expires, time.time() + 300)
        except (ValueError, KeyError, jwt.PyJWTError):
            raise GitHubClientError("github_auth_error") from None

    def invalidate_tokens(self):
        with self._token_lock:
            self._tokens.clear()

    def app_client(self):
        """Client authenticated as the App itself (JWT), for endpoints like GET /app."""
        from githubapp.client import GitHubClient
        return GitHubClient(self._generate_jwt())
