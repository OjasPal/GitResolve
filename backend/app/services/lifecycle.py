"""Pure lifecycle rules: allowed transitions, active-slot statuses, actions.

No I/O here. Backend Schema section 4 and TRD section 4 are the source.
"""

from app.schemas.enums import Action, FailureCode, TaskStatus

S = TaskStatus

ACTIVE_STATUSES = frozenset({S.QUEUED, S.PLANNING, S.RUNNING, S.CREATING_PR})
HUMAN_GATE_STATUSES = frozenset({S.AWAITING_START, S.AWAITING_APPROVAL})
FAILURE_STATUSES = frozenset(
    {
        S.FAILED_NOT_REPRODUCED,
        S.FAILED_NO_FIX,
        S.FAILED_POLICY,
        S.FAILED_BUDGET,
        S.TIMED_OUT,
        S.FAILED_INFRA,
        S.WORKER_LOST,
    }
)
TERMINAL_STATUSES = frozenset(
    {S.COMPLETED, S.NEEDS_INPUT, S.REJECTED, S.CANCELLED} | FAILURE_STATUSES
)
# Terminal statuses that may re-enter creating_pr (only when failure.phase == "pr").
PR_RETRY_STATUSES = frozenset({S.FAILED_INFRA, S.WORKER_LOST})

_RUN_FAILURES = frozenset(FAILURE_STATUSES)

TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    S.QUEUED: frozenset({S.PLANNING, S.CANCELLED, S.TIMED_OUT}),
    S.PLANNING: frozenset(
        {S.AWAITING_START, S.NEEDS_INPUT, S.CANCELLED, S.FAILED_INFRA, S.WORKER_LOST, S.TIMED_OUT, S.FAILED_BUDGET}
    ),
    S.AWAITING_START: frozenset({S.RUNNING, S.CANCELLED}),
    S.RUNNING: frozenset({S.AWAITING_APPROVAL, S.CANCELLED} | _RUN_FAILURES),
    S.AWAITING_APPROVAL: frozenset({S.CREATING_PR, S.REJECTED}),
    S.CREATING_PR: frozenset({S.COMPLETED, S.FAILED_INFRA, S.WORKER_LOST, S.TIMED_OUT}),
    S.FAILED_INFRA: frozenset({S.CREATING_PR}),
    S.WORKER_LOST: frozenset({S.CREATING_PR}),
}

FAILURE_CODE_TO_STATUS: dict[FailureCode, TaskStatus] = {
    FailureCode.ISSUE_AMBIGUOUS: S.NEEDS_INPUT,
    FailureCode.NOT_REPRODUCED: S.FAILED_NOT_REPRODUCED,
    FailureCode.NO_FIX_FOUND: S.FAILED_NO_FIX,
    FailureCode.POLICY_VIOLATION: S.FAILED_POLICY,
    FailureCode.BUDGET_EXCEEDED: S.FAILED_BUDGET,
    FailureCode.TIMEOUT: S.TIMED_OUT,
    FailureCode.GITHUB_RATE_LIMITED: S.FAILED_INFRA,
    FailureCode.GITHUB_ERROR: S.FAILED_INFRA,
    FailureCode.ACCESS_REVOKED: S.FAILED_INFRA,
    FailureCode.LLM_UNAVAILABLE: S.FAILED_INFRA,
    FailureCode.SANDBOX_ERROR: S.FAILED_INFRA,
    FailureCode.INTERNAL_ERROR: S.FAILED_INFRA,
    FailureCode.WORKER_LOST: S.WORKER_LOST,
}


def status_for_failure(code: FailureCode) -> TaskStatus:
    return FAILURE_CODE_TO_STATUS[code]


def is_active(status: str) -> bool:
    return status in ACTIVE_STATUSES


def can_transition(current: str, target: str) -> bool:
    return TaskStatus(target) in TRANSITIONS.get(TaskStatus(current), frozenset())


def available_actions(status: str, failure_phase: str | None) -> list[Action]:
    """Actions the UI may offer (AFD section 6). Advisory; the API re-validates."""
    s = TaskStatus(status)
    if s in (S.QUEUED, S.PLANNING, S.RUNNING):
        return [Action.CANCEL]
    if s == S.AWAITING_START:
        return [Action.START, Action.CANCEL]
    if s == S.AWAITING_APPROVAL:
        return [Action.APPROVE, Action.REJECT]
    if s == S.CREATING_PR:
        return []
    if s in PR_RETRY_STATUSES and failure_phase == "pr":
        return [Action.RETRY_PR, Action.RUN_AGAIN]
    return [Action.RUN_AGAIN]
