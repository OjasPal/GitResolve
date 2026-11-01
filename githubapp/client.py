"""Minimal read-only GitHub API client."""

import re

import requests

API = "https://api.github.com"
NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


class GitHubClientError(Exception):
    pass


class GitHubClient:
    def __init__(self, token: str):
        if not token:
            raise ValueError("token is required")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _get(self, path: str) -> dict:
        res = requests.get(f"{API}{path}", headers=self._headers, timeout=15)
        if res.status_code == 404:
            raise GitHubClientError("not_found_or_no_access")
        if res.status_code in (401, 403):
            raise GitHubClientError("permission_denied")
        res.raise_for_status()
        return res.json()

    @staticmethod
    def _check(owner: str, repo: str) -> None:
        if not (NAME.match(owner) and NAME.match(repo)):
            raise GitHubClientError("invalid_owner_or_repo")

    def get_repo(self, owner: str, repo: str) -> dict:
        self._check(owner, repo)
        data = self._get(f"/repos/{owner}/{repo}")
        return {"full_name": data["full_name"], "default_branch": data["default_branch"], "private": data["private"]}

    def resolve_branch_sha(self, owner: str, repo: str, branch: str) -> str:
        self._check(owner, repo)
        if not re.match(r"^[A-Za-z0-9_./-]{1,200}$", branch):
            raise GitHubClientError("invalid_branch")
        return self._get(f"/repos/{owner}/{repo}/branches/{branch}")["commit"]["sha"]