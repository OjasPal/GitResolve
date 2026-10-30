import itertools

from app.schemas.enums import Action, FailureCode, TaskStatus
from app.services import lifecycle as lc

S = TaskStatus


def test_every_failure_code_maps_to_a_status():
    assert set(lc.FAILURE_CODE_TO_STATUS) == set(FailureCode)


def test_every_status_is_classified():
    classified = lc.ACTIVE_STATUSES | lc.HUMAN_GATE_STATUSES | lc.TERMINAL_STATUSES
    assert classified == set(TaskStatus)


def test_active_slot_statuses_match_schema():
    assert lc.ACTIVE_STATUSES == {S.QUEUED, S.PLANNING, S.RUNNING, S.CREATING_PR}


def test_terminal_statuses_have_no_outgoing_transitions_except_pr_retry():
    for status in lc.TERMINAL_STATUSES:
        outgoing = lc.TRANSITIONS.get(status, frozenset())
        if status in lc.PR_RETRY_STATUSES:
            assert outgoing == {S.CREATING_PR}
        else:
            assert outgoing == frozenset()


def test_happy_path_is_allowed_and_shortcuts_are_not():
    path = [S.QUEUED, S.PLANNING, S.AWAITING_START, S.RUNNING, S.AWAITING_APPROVAL, S.CREATING_PR, S.COMPLETED]
    for a, b in itertools.pairwise(path):
        assert lc.can_transition(a, b)
    assert not lc.can_transition(S.QUEUED, S.RUNNING)  # plan gate cannot be skipped
    assert not lc.can_transition(S.RUNNING, S.CREATING_PR)  # approval gate cannot be skipped
    assert not lc.can_transition(S.AWAITING_START, S.AWAITING_APPROVAL)
    assert not lc.can_transition(S.AWAITING_APPROVAL, S.CANCELLED)
    assert not lc.can_transition(S.CREATING_PR, S.CANCELLED)  # creating_pr is not cancellable


def test_available_actions_follow_the_afd_table():
    assert lc.available_actions("queued", None) == [Action.CANCEL]
    assert lc.available_actions("awaiting_start", None) == [Action.START, Action.CANCEL]
    assert lc.available_actions("awaiting_approval", None) == [Action.APPROVE, Action.REJECT]
    assert lc.available_actions("creating_pr", None) == []
    assert lc.available_actions("completed", None) == [Action.RUN_AGAIN]
    assert lc.available_actions("failed_infra", "pr") == [Action.RETRY_PR, Action.RUN_AGAIN]
    assert lc.available_actions("failed_infra", "execute") == [Action.RUN_AGAIN]
