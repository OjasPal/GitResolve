"""GitHub App gateway. Credentials are host-only and never stored in task data."""
from pathlib import Path
from urllib.parse import urlencode, quote

import requests

from githubapp.auth import GitHubAuth
from githubapp.client import GitHubClient, GitHubClientError
from app.services.github import GitHubUnavailable, GitHubIdentity, RepositoryInfo, IssueInfo


class RealGitHubGateway:
    def __init__(self, settings, store, auth=None):
        self.settings, self.store = settings, store
        key = settings.github_app_private_key
        if not key and settings.github_app_private_key_path:
            key = Path(settings.github_app_private_key_path).read_text(encoding="utf-8")
        self.auth = auth or GitHubAuth(str(settings.github_app_id or ""), key or "")

    def _client(self, installation, repo=None, write=False):
        permissions = {"metadata": "read"}
        if repo:
            permissions["contents"] = "write" if write else "read"
            permissions["pull_requests" if write else "issues"] = "write" if write else "read"
        def mint():
            return self.auth.get_installation_token(
                installation, repositories=[repo] if repo else None, permissions=permissions)
        return GitHubClient(mint(), refresh=mint)

    def _repositories(self, user_id):
        user = self.store.get(f"users/{user_id}") or {}
        for installation in user.get("installationIds", []):
            if self.settings.github_installation_id and installation != self.settings.github_installation_id:
                continue
            client = self._client(installation)
            for repo in client.pages("/installation/repositories", "repositories"):
                # OAuth installation membership alone does not establish repository access.
                permission = client._get(f"/repos/{repo['full_name']}/collaborators/{quote(user['githubLogin'], safe='')}/permission")
                if permission.get("user", {}).get("id") != int(user_id):
                    continue
                if permission.get("permission") not in {"read", "triage", "write", "maintain", "admin"}:
                    continue
                yield RepositoryInfo(f"{installation}_{repo['id']}", installation,
                    repo["full_name"], repo["default_branch"], repo["private"], repo.get("language"))

    def list_repositories(self, user_id):
        try:
            return list(self._repositories(user_id))
        except GitHubClientError as exc:
            raise GitHubUnavailable(exc.code) from None

    def get_repository(self, user_id, repo_id):
        return next((r for r in self.list_repositories(user_id) if r.repo_id == repo_id), None)

    def authorized_client(self, user_id, repo_id, *, write=False):
        repo = self.get_repository(user_id, repo_id)
        if repo is None:
            raise GitHubClientError("access_revoked")
        client = self._client(repo.installation_id, repo.full_name.split('/')[1], write)
        if write:
            user = self.store.get(f"users/{user_id}")
            permission = client._get(f"/repos/{repo.full_name}/collaborators/{quote(user['githubLogin'], safe='')}/permission")
            if permission.get("permission") not in {"write", "maintain", "admin"}:
                raise GitHubClientError("access_revoked")
        return repo, client

    @staticmethod
    def _issue(data, comments=None):
        return IssueInfo(data["number"], data["title"], data["html_url"],
                         (data.get("body") or "")[:30000],
                         [label["name"] for label in data.get("labels", [])][:50],
                         comments or [], data["state"])

    def list_open_issues(self, user_id, repo_id):
        try:
            repo, client = self.authorized_client(user_id, repo_id)
            return [self._issue(d) for d in client.pages(f"/repos/{repo.full_name}/issues", state="open")
                    if "pull_request" not in d]
        except GitHubClientError as exc:
            raise GitHubUnavailable(exc.code) from None

    def get_issue(self, user_id, repo_id, number):
        try:
            repo, client = self.authorized_client(user_id, repo_id)
            path = f"/repos/{repo.full_name}/issues/{int(number)}"
            data = client._get(path)
            if data.get("state") != "open" or "pull_request" in data:
                return None
            comments = client.request("GET", path + "/comments", params={"per_page": 20})
            return self._issue(data, [{"author": (c.get("user") or {}).get("login", "deleted"),
                                      "body": (c.get("body") or "")[:4000]} for c in comments])
        except GitHubClientError as exc:
            if exc.code == "access_revoked":
                return None
            raise GitHubUnavailable(exc.code) from None

    def resolve(self, task):
        repo, client = self.authorized_client(task["userId"], task["repoId"])
        owner, name = repo.full_name.split('/')
        return client.resolve_branch_sha(owner, name, repo.default_branch)

    def source(self, task):
        """Trusted callback, never a serializable sandbox request or credential mount."""
        def acquire(request, workspace, deadline, cancel):
            from githubapp.checkout import acquire_archive
            repo, client = self.authorized_client(task["userId"], task["repoId"])
            if request.repo_url != f"https://github.com/{repo.full_name}":
                raise GitHubClientError("access_revoked")
            return acquire_archive(client, repo.full_name, request.commit_sha, workspace, deadline, cancel)
        return acquire


class AppOAuthProvider:
    def __init__(self, settings):
        self.settings = settings

    def authorization_url(self, state, redirect_uri):
        return "https://github.com/login/oauth/authorize?" + urlencode({
            "client_id": self.settings.github_app_client_id, "redirect_uri": redirect_uri, "state": state})

    def authenticate(self, code, redirect_uri):
        try:
            response = requests.post("https://github.com/login/oauth/access_token", json={
                "client_id": self.settings.github_app_client_id,
                "client_secret": self.settings.github_app_client_secret,
                "code": code, "redirect_uri": redirect_uri},
                headers={"Accept": "application/json"}, timeout=15, allow_redirects=False)
            if response.status_code != 200:
                raise GitHubClientError()
            client = GitHubClient(response.json()["access_token"])
            user = client._get("/user")
            installations = client.pages("/user/installations", "installations")
            return GitHubIdentity(str(user["id"]), user["login"], user.get("avatar_url"),
                                  [i["id"] for i in installations])
        except (requests.RequestException, GitHubClientError, KeyError, ValueError):
            raise GitHubUnavailable("github_auth_error") from None
