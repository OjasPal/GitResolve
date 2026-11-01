"""GitHub App authentication. Tokens live in memory only: never log or persist them."""

import time

import jwt  # PyJWT[crypto]
import requests

API = "https://api.github.com"


class GitHubAuth:
    def __init__(self, app_id: str, private_key_pem: str):
        if not app_id or not private_key_pem:
            raise ValueError("app_id and private_key_pem are required")
        self.app_id = app_id
        self._private_key = private_key_pem

    def _generate_jwt(self) -> str:
        now = int(time.time())
        payload = {"iat": now - 60, "exp": now + 9 * 60, "iss": self.app_id}
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    def get_installation_token(
        self,
        installation_id: int,
        repositories: list[str] | None = None,
        permissions: dict[str, str] | None = None,
    ) -> str:
        """Short-lived (~1h) token. Pass repositories/permissions to narrow it further."""
        body: dict = {}
        if repositories:
            body["repositories"] = repositories
        if permissions:
            body["permissions"] = permissions
        res = requests.post(
            f"{API}/app/installations/{int(installation_id)}/access_tokens",
            headers={
                "Authorization": f"Bearer {self._generate_jwt()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json=body or None,
            timeout=15,
        )
        res.raise_for_status()
        return res.json()["token"]