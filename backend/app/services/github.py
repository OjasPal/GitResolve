"""GitHub boundary used by the API.

The API needs two things from GitHub: verify repository access through the
user's installations, and read an issue snapshot. The real implementation
(GitHub App tokens) replaces `FakeGitHubGateway`; the interface is the contract.
"""

from dataclasses import dataclass, field
from typing import Protocol


class GitHubUnavailable(Exception):
    """GitHub returned an error, timed out, or rate-limited the request."""


@dataclass(frozen=True)
class RepositoryInfo:
    repo_id: str  # "{installationId}_{githubRepoId}"
    installation_id: int
    full_name: str
    default_branch: str
    private: bool = False
    language: str | None = "Python"


@dataclass(frozen=True)
class IssueInfo:
    number: int
    title: str
    url: str
    body: str = ""
    labels: list[str] = field(default_factory=list)
    comments: list[dict] = field(default_factory=list)  # [{"author", "body"}]
    state: str = "open"


@dataclass(frozen=True)
class GitHubIdentity:
    user_id: str
    login: str
    avatar_url: str | None
    installation_ids: list[int]


class GitHubOAuthProvider(Protocol):
    """GitHub OAuth boundary consumed by the API auth routes."""

    def authorization_url(self, state: str, redirect_uri: str) -> str: ...

    def authenticate(self, code: str, redirect_uri: str) -> GitHubIdentity: ...


class GitHubGateway(Protocol):
    def list_repositories(self, user_id: str) -> list[RepositoryInfo]: ...

    def get_repository(self, user_id: str, repo_id: str) -> RepositoryInfo | None:
        """None when the user has no installation access to the repository."""

    def list_open_issues(self, user_id: str, repo_id: str) -> list[IssueInfo]: ...

    def get_issue(self, user_id: str, repo_id: str, number: int) -> IssueInfo | None:
        """None when the issue does not exist or is not open."""


class FakeGitHubGateway:
    """Deterministic local stand-in. Any user sees the same demo repository."""

    REPO = RepositoryInfo(
        repo_id="1001_2002",
        installation_id=1001,
        full_name="demo/seeded-bugs",
        default_branch="main",
    )

    def __init__(self) -> None:
        self._issues = {
            1: IssueInfo(
                number=1,
                title="parse_duration crashes on empty string",
                url="https://github.com/demo/seeded-bugs/issues/1",
                body="Calling parse_duration('') raises IndexError instead of ValueError.",
                labels=["bug"],
            ),
            2: IssueInfo(
                number=2,
                title="slugify keeps trailing hyphens",
                url="https://github.com/demo/seeded-bugs/issues/2",
                body="slugify('Hello, world!') returns 'hello-world-'.",
                labels=["bug"],
            ),
        }
        self.unavailable = False  # tests flip this to simulate GitHub outages

    def _check(self) -> None:
        if self.unavailable:
            raise GitHubUnavailable("GitHub is unavailable.")

    def list_repositories(self, user_id):
        self._check()
        return [self.REPO]

    def get_repository(self, user_id, repo_id):
        self._check()
        return self.REPO if repo_id == self.REPO.repo_id else None

    def list_open_issues(self, user_id, repo_id):
        self._check()
        return list(self._issues.values()) if repo_id == self.REPO.repo_id else []

    def get_issue(self, user_id, repo_id, number):
        self._check()
        if repo_id != self.REPO.repo_id:
            return None
        return self._issues.get(number)
