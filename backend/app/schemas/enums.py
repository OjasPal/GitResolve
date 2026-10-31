"""Shared enums: single source of truth for statuses, stages and codes.

Values are snake_case strings (Backend Schema, section 1). Frontend types are
generated from the FastAPI OpenAPI document, which embeds these enums.
"""

from enum import StrEnum


class TaskStatus(StrEnum):
    # Active (occupy the user's single active-task slot)
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    CREATING_PR = "creating_pr"
    # Human gates (slot released while waiting)
    AWAITING_START = "awaiting_start"
    AWAITING_APPROVAL = "awaiting_approval"
    # Success
    COMPLETED = "completed"
    # Stopped
    NEEDS_INPUT = "needs_input"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    # Failure
    FAILED_NOT_REPRODUCED = "failed_not_reproduced"
    FAILED_NO_FIX = "failed_no_fix"
    FAILED_POLICY = "failed_policy"
    FAILED_BUDGET = "failed_budget"
    TIMED_OUT = "timed_out"
    FAILED_INFRA = "failed_infra"
    WORKER_LOST = "worker_lost"


class TaskStage(StrEnum):
    INTAKE = "intake"
    PLAN = "plan"
    SETUP = "setup"
    BASELINE = "baseline"
    LOCATE = "locate"
    REPRODUCE = "reproduce"
    PATCH = "patch"
    TEST = "test"
    EVALUATE = "evaluate"
    POLICY_GATE = "policy_gate"
    AWAITING_APPROVAL = "awaiting_approval"
    PR = "pr"
    COMPLETED = "completed"


class DispatchPhase(StrEnum):
    PLAN = "plan"
    EXECUTE = "execute"
    PR = "pr"


class FailureCode(StrEnum):
    ISSUE_AMBIGUOUS = "issue_ambiguous"
    NOT_REPRODUCED = "not_reproduced"
    NO_FIX_FOUND = "no_fix_found"
    POLICY_VIOLATION = "policy_violation"
    BUDGET_EXCEEDED = "budget_exceeded"
    TIMEOUT = "timeout"
    GITHUB_RATE_LIMITED = "github_rate_limited"
    GITHUB_ERROR = "github_error"
    ACCESS_REVOKED = "access_revoked"
    LLM_UNAVAILABLE = "llm_unavailable"
    SANDBOX_ERROR = "sandbox_error"
    INTERNAL_ERROR = "internal_error"
    WORKER_LOST = "worker_lost"


class EventLevel(StrEnum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class EventType(StrEnum):
    STATUS_CHANGE = "status_change"
    STAGE_CHANGE = "stage_change"
    TOOL_CALL = "tool_call"
    LLM_CALL = "llm_call"
    TEST_RUN = "test_run"
    POLICY_CHECK = "policy_check"
    ATTEMPT = "attempt"
    APPROVAL = "approval"
    PR_CREATED = "pr_created"
    CANCELLATION = "cancellation"
    ERROR = "error"
    INFO = "info"


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ArtifactType(StrEnum):
    PLAN = "plan"
    REPRO_TEST = "repro_test"
    ATTEMPT_DIFF = "attempt_diff"
    ATTEMPT_TESTS = "attempt_tests"
    FINAL_DIFF = "final_diff"
    FINAL_TESTS = "final_tests"


class Action(StrEnum):
    """Actions the UI may offer for a task in its current state."""

    START = "start"
    CANCEL = "cancel"
    APPROVE = "approve"
    REJECT = "reject"
    RETRY_PR = "retry_pr"
    RUN_AGAIN = "run_again"
