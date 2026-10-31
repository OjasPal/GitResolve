"""Contract between the worker and the agent.

The agent never writes to Firestore and never sets task statuses. It returns a
result object; the backend maps it to a status via `FAILURE_CODE_TO_STATUS`.
Between graph nodes the agent calls `ctx.checkpoint()` and reports progress
with `ctx.stage(...)`, `ctx.event(...)`, `ctx.artifact(...)`, `ctx.usage(...)`.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

from app.schemas.enums import FailureCode
from app.schemas.tasks import PlanData

if TYPE_CHECKING:
    from app.worker.context import JobContext


@dataclass(frozen=True)
class AgentTask:
    """Read-only task snapshot handed to the agent."""

    task_id: str
    repo_id: str
    repo_full_name: str
    default_branch: str
    base_sha: str | None
    issue: dict[str, Any]  # number, title, url, body, labels, comments
    plan: dict[str, Any] | None
    limits: dict[str, Any]


@dataclass
class PlanResult:
    outcome: Literal["ready", "needs_input", "failed"]
    plan: PlanData | None = None  # required for "ready"; questions go here for "needs_input"
    failure_code: FailureCode | None = None
    failure_message: str = ""


@dataclass
class ExecuteResult:
    outcome: Literal["fix_ready", "failed"]
    final_diff: str = ""  # exact unified diff that would be used for the PR
    final_tests: str = ""  # redacted before/after test evidence (bounded)
    summary: dict[str, Any] = field(default_factory=dict)  # filesChanged, linesAdded, linesRemoved, files[]
    failure_code: FailureCode | None = None
    failure_message: str = ""
    verification: dict[str, Any] = field(default_factory=dict)


class AgentRunner(Protocol):
    def plan(self, task: AgentTask, ctx: "JobContext") -> PlanResult: ...

    def execute(self, task: AgentTask, ctx: "JobContext") -> ExecuteResult: ...
