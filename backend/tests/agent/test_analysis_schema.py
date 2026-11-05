
from agent.analysis_schema import IssueAnalysis


def test_issue_analysis_accepts_structured_output():
    analysis = IssueAnalysis(
        problem_summary="Login fails for invalid email addresses.",
        investigation_steps=[
            "Inspect login validation.",
            "Review related tests.",
        ],
        needs_more_information=False,
        questions_for_reporter=[],
    )

    assert len(analysis.investigation_steps) == 2
    assert analysis.needs_more_information is False
