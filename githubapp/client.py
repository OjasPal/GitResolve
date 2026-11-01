"""Bounded GitHub API client with sanitized errors and explicit write methods."""

import re

from urllib.parse import quote
import time

import requests

API = "https://api.github.com"
NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


class GitHubClientError(Exception):
    """Stable, secret-free error; never include upstream bodies or URLs."""
    def __init__(self, code="github_error"):
        self.code = code
        super().__init__(code)



class GitHubClient:
    def __init__(self, token: str, refresh=None):
        if not token:
            raise ValueError("token is required")
        self._refresh = refresh
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def request(self, method: str, path: str, **kwargs):
        if not path.startswith("/") or "?" in path or "#" in path:
            raise GitHubClientError("invalid_path")
        if method not in {"GET", "POST"}:
            raise GitHubClientError("unsupported_method")
        refreshed = False
        for attempt in range(3):
            try:
                call = requests.get if method == "GET" else requests.post
                res = call(f"{API}{path}", headers=self._headers, timeout=15,
                           allow_redirects=False, **kwargs)
            except requests.RequestException:
                if method == "GET" and attempt < 2:
                    time.sleep(0.25 * (2 ** attempt))
                    continue
                raise GitHubClientError("github_error") from None
            headers = getattr(res, "headers", {})
            if res.status_code == 429 or (res.status_code == 403 and
                    (headers.get("X-RateLimit-Remaining") == "0" or headers.get("Retry-After"))):
                raise GitHubClientError("github_rate_limited")
            if res.status_code == 401 and self._refresh and not refreshed:
                self._headers["Authorization"] = f"Bearer {self._refresh()}"
                refreshed = True
                continue
            if res.status_code in (401, 403, 404):
                raise GitHubClientError("access_revoked")
            if res.status_code >= 500 and method == "GET" and attempt < 2:
                time.sleep(0.25 * (2 ** attempt))
                continue
            if not 200 <= res.status_code < 300:
                raise GitHubClientError("github_error")
            try:
                return res.json()
            except (ValueError, TypeError):
                raise GitHubClientError("github_error") from None
        raise GitHubClientError("github_error")

    def _get(self, path: str):
        return self.request("GET", path)

    def pages(self, path: str, key=None, **params):
        items = []
        for page in range(1, 11):
            data = self.request("GET", path, params={**params, "per_page": 100, "page": page})
            batch = data[key] if key else data
            items.extend(batch)
            if len(batch) < 100:
                return items
        raise GitHubClientError("github_result_limit")

    @staticmethod
    def _check(owner: str, repo: str) -> None:
        if not (NAME.fullmatch(owner) and NAME.fullmatch(repo)) or owner in {".", ".."} or repo in {".", ".."}:
            raise GitHubClientError("invalid_owner_or_repo")

    def get_repo(self, owner: str, repo: str) -> dict:
        self._check(owner, repo)
        data = self._get(f"/repos/{owner}/{repo}")
        return data

    def resolve_branch_sha(self, owner: str, repo: str, branch: str) -> str:
        self._check(owner, repo)
        if not re.fullmatch(r"[A-Za-z0-9_./-]{1,200}", branch) or ".." in branch or "//" in branch:
            raise GitHubClientError("invalid_branch")
        return self._get(f"/repos/{owner}/{repo}/branches/{quote(branch, safe='')}")["commit"]["sha"]