"""GitHub App gateway. Credentials are host-only and never stored in task data."""
from pathlib import Path
from urllib.parse import urlencode, quote
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from githubapp.cache import TTLCache

from githubapp.timing import timed

import requests

from githubapp.auth import GitHubAuth
from githubapp.client import GitHubClient, GitHubClientError
from githubapp.identity import resolve_bot_identity
from app.services.github import GitHubUnavailable, GitHubIdentity, RepositoryInfo, IssueInfo


class RealGitHubGateway:
    def __init__(self, settings, store, auth=None):
        self.settings, self.store = settings, store
        key = settings.github_app_private_key
        if not key and settings.github_app_private_key_path:
            key = Path(settings.github_app_private_key_path).read_text(encoding="utf-8")
        self.auth = auth or GitHubAuth(str(settings.github_app_id or ""), key or "")
        self._bot_identity = None
        self._listing_cache = TTLCache()
        self._issue_cache = TTLCache()
        self._etag_cache = TTLCache(ttl=300)
        self._locator_cache = TTLCache(ttl=300)

    def _client(self, installation, repo=None, write=False):
        permissions = {"metadata": "read"}
        if repo:
            permissions["contents"] = "write" if write else "read"
            permissions["pull_requests" if write else "issues"] = "write" if write else "read"
        def mint():
            return self.auth.get_installation_token(
                installation, repositories=[repo] if repo else None, permissions=permissions)
        def refresh():
            self._listing_cache.clear()
            self._issue_cache.clear()
            return self.auth.get_installation_token(installation,
                repositories=[repo] if repo else None, permissions=permissions, force_refresh=True)
        def rejected():
            self._listing_cache.clear()
            self._issue_cache.clear()
            self._etag_cache.clear()
            self._locator_cache.clear()
            if hasattr(self.auth, 'invalidate_tokens'):
                self.auth.invalidate_tokens()
        return GitHubClient(mint(), refresh=refresh, on_rejected=rejected)

    def _repositories(self, user_id, page=None, more=None):
        user = self.store.get(f"users/{user_id}") or {}
        if not user.get("githubLogin"):
            return
        # Discover installations across owners, then authorize every repository
        # against the signed-in GitHub identity before exposing it.
        installations = self.auth.app_client().pages("/app/installations")
        for installed in installations:
            if installed.get("suspended_at"):
                continue
            installation = installed["id"]
            if self.settings.github_installation_id and installation != self.settings.github_installation_id:
                continue
            try:
                client = self._client(installation)
                if page is None:
                    repositories = client.pages("/installation/repositories", "repositories")
                else:
                    repositories = client.request('GET', '/installation/repositories',
                        etag_cache=self._etag_cache, cache_key=(str(user_id), installation, page),
                        params={'per_page': 30, 'page': page})['repositories']
                    if len(repositories) == 30:
                        more.append(True)
            except GitHubClientError as exc:
                if exc.code == "access_revoked":
                    continue
                raise
            def authorize(repo):
                # OAuth installation membership alone does not establish repository access.
                try:
                    permission_client = self._client(installation)
                    permission = permission_client._get(f"/repos/{repo['full_name']}/collaborators/{quote(user['githubLogin'], safe='')}/permission")
                except GitHubClientError as exc:
                    if exc.code == "access_revoked":
                        return None
                    raise
                if permission.get("user", {}).get("id") != int(user_id):
                    return None
                # Public visibility is not permission to request repository changes.
                if permission.get("permission") not in {"write", "maintain", "admin"}:
                    return None
                return RepositoryInfo(f"{installation}_{repo['id']}", installation,
                    repo["full_name"], repo["default_branch"], repo["private"], repo.get("language"))
            # Each request has its own copied timing context; no shared mutable client
            # refresh occurs in normal permission reads. Writes remain sequential.
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(copy_context().run, authorize, repo) for repo in repositories]
                for future in futures:
                    repo = future.result()
                    if repo is not None:
                        yield repo

    @timed("github.repositories")
    def list_repositories(self, user_id):
        try:
            user = self.store.get(f"users/{user_id}") or {}
            key = (str(user_id), user.get("githubLogin"), self.settings.github_installation_id)
            cached = self._listing_cache.get(key)
            if cached is not None:
                return cached
            repos = list(self._repositories(user_id))
            self._listing_cache.put(key, repos)
            self._locator_cache.put(key, repos)
            return repos
        except GitHubClientError as exc:
            raise GitHubUnavailable(exc.code) from None

    def get_repository(self, user_id, repo_id):
        try:
            return self._get_repository(user_id, repo_id)
        except GitHubClientError as exc:
            raise GitHubUnavailable(exc.code) from None

    def _get_repository(self, user_id, repo_id):
        # Authorization deliberately bypasses the display cache, including publication.
        user = self.store.get(f"users/{user_id}") or {}
        key = (str(user_id), user.get("githubLogin"), self.settings.github_installation_id)
        candidates = self._locator_cache.get(key) or []
        known = next((r for r in candidates if r.repo_id == repo_id), None)
        if known is not None:
            try:
                client = self._client(known.installation_id, known.full_name.split('/')[1])
                owner, name = known.full_name.split('/')
                current = client.get_repo(owner, name)
                permission = client._get(f"/repos/{known.full_name}/collaborators/{quote(user['githubLogin'], safe='')}/permission")
                if (str(current['id']) == repo_id.split('_')[1]
                        and current['full_name'] == known.full_name
                        and permission.get('user', {}).get('id') == int(user_id)
                        and permission.get('permission') in {'write', 'maintain', 'admin'}):
                    return RepositoryInfo(repo_id, known.installation_id, current['full_name'],
                        current['default_branch'], current['private'], current.get('language'))
            except GitHubClientError as exc:
                if exc.code != 'access_revoked':
                    raise
            self._listing_cache.clear()
            self._issue_cache.clear()
            self._locator_cache.clear()
            return None
        return next((r for r in self._repositories(user_id) if r.repo_id == repo_id), None)

    @timed('github.repositories.page')
    def repository_page(self, user_id, page=1):
        user = self.store.get(f'users/{user_id}') or {}
        identity = (str(user_id), user.get('githubLogin'), self.settings.github_installation_id)
        key = (*identity, page)
        cached = self._listing_cache.get(key)
        if cached is not None:
            return cached
        try:
            more = []
            repos = list(self._repositories(user_id, page, more))
            result = (repos, page + 1 if more else None)
            self._listing_cache.put(key, result)
            # Locator hints only. get_repository still performs fresh authorization.
            known = {r.repo_id: r for r in (self._locator_cache.get(identity) or [])}
            known.update({r.repo_id: r for r in repos})
            self._locator_cache.put(identity, list(known.values()))
            return result
        except GitHubClientError as exc:
            raise GitHubUnavailable(exc.code) from None

    @timed('github.issues.page')
    def issue_page(self, user_id, repo_id, page=1):
        user = self.store.get(f'users/{user_id}') or {}
        key = (str(user_id), user.get('githubLogin'), repo_id, page)
        cached = self._issue_cache.get(key)
        if cached is not None:
            return cached
        try:
            repo, client = self.authorized_client(user_id, repo_id)
            rows = client.request('GET', f'/repos/{repo.full_name}/issues',
                etag_cache=self._etag_cache, cache_key=(str(user_id), repo_id, page),
                params={'state': 'open', 'per_page': 30, 'page': page})
            result = ([self._issue(row) for row in rows if 'pull_request' not in row],
                      page + 1 if len(rows) == 30 else None)
            self._issue_cache.put(key, result)
            return result
        except GitHubClientError as exc:
            raise GitHubUnavailable(exc.code) from None

    def authorized_client(self, user_id, repo_id, *, write=False):
        repo = self.get_repository(user_id, repo_id)
        if repo is None:
            raise GitHubClientError("access_revoked")
        client = self._client(repo.installation_id, repo.full_name.split('/')[1], write)
        if write:
            user = self.store.get(f"users/{user_id}")
            permission = client._get(f"/repos/{repo.full_name}/collaborators/{quote(user['githubLogin'], safe='')}/permission")
            if (permission.get("permission") not in {"write", "maintain", "admin"}
                    or permission.get("user", {}).get("id") != int(user_id)):
                raise GitHubClientError("access_revoked")
        return repo, client

    def verified_bot_identity(self, client):
        """GitResolve Agent bot identity, verified against GitHub once and cached.

        Raises GitHubClientError on any mismatch; there is no fallback identity.
        """
        if self._bot_identity is None:
            self._bot_identity = resolve_bot_identity(
                self.auth.app_client(), client, self.auth.app_id)
        return self._bot_identity

    @staticmethod
    def _issue(data, comments=None):
        return IssueInfo(data["number"], data["title"], data["html_url"],
                         (data.get("body") or "")[:30000],
                         [label["name"] for label in data.get("labels", [])][:50],
                         comments or [], data["state"])

    @timed("github.issues")
    def list_open_issues(self, user_id, repo_id):
        try:
            user = self.store.get(f"users/{user_id}") or {}
            key = (str(user_id), user.get("githubLogin"), repo_id)
            cached = self._issue_cache.get(key)
            if cached is not None:
                return cached
            repo, client = self.authorized_client(user_id, repo_id)
            issues = [self._issue(d) for d in client.pages(f"/repos/{repo.full_name}/issues", state="open")
                      if "pull_request" not in d]
            self._issue_cache.put(key, issues)
            return issues
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
