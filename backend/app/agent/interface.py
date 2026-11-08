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

StructuredOutcome = Literal[
    "fix_ready", "needs_user_action", "needs_clarification", "cannot_reproduce",
    "out_of_scope", "cannot_fix_with_report", "infra_retry",
]

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
    attempts_used: int = 0
    cleanup_scope: dict[str, Any] | None = None

@dataclass
class PlanResult:
    outcome: StructuredOutcome | Literal["ready", "needs_input", "failed"]
    plan: PlanData | None = None  # required for "ready"; questions go here for "needs_input"
    failure_code: FailureCode | None = None
    failure_message: str = ""
    evidence: list[str] = field(default_factory=list)
    next_step: str = ""

    def __post_init__(self):
        from app.core.errors import safe_message
        self.failure_message = safe_message(self.failure_message, 500)
        self.evidence = list(self.evidence or [])


@dataclass
class ExecuteResult:
    outcome: StructuredOutcome | Literal["failed"]
    final_diff: str = ""  # exact unified diff that would be used for the PR
    final_tests: str = ""  # redacted before/after test evidence (bounded)
    summary: dict[str, Any] = field(default_factory=dict)  # filesChanged, linesAdded, linesRemoved, files[]
    failure_code: FailureCode | None = None
    failure_message: str = ""
    verification: dict[str, Any] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    next_step: str = ""

    def __post_init__(self):
        from app.core.errors import safe_message
        self.failure_message = safe_message(self.failure_message, 500)
        self.evidence = list(self.evidence or [])


class AgentRunner(Protocol):
    def plan(self, task: AgentTask, ctx: "JobContext") -> PlanResult: ...

    def execute(self, task: AgentTask, ctx: "JobContext") -> ExecuteResult: ...
