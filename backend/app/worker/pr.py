"""Pull-request boundary used by the worker."""

from dataclasses import dataclass
from typing import Protocol

from app.schemas.enums import FailureCode


class PullRequestError(Exception):
    def __init__(self, code: FailureCode, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PullRequestResult:
    number: int
    url: str
    branch: str


class PullRequestCreator(Protocol):
    def create(self, task: dict, diff: str) -> PullRequestResult:
        """Check out `baseSha`, reapply the approved diff, verify its hash, then
        create the branch, commit and PR. Raise PullRequestError on failure."""


class FakePullRequestCreator:
    """LOCAL ONLY. Returns a placeholder; no GitHub call is made."""

    def create(self, task: dict, diff: str) -> PullRequestResult:
        number = int(task["issue"]["number"])
        return PullRequestResult(
            number=number,
            url=f"https://example.invalid/{task['repoFullName']}/pull/{number}",
            branch=f"gitresolve/issue-{number}-{task['id'][4:10]}",
        )
