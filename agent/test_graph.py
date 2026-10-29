
import agent.graph as graph_module
from agent.analysis_schema import IssueAnalysis


def make_initial_state():
    return {
        "task_id": "tsk_test",
        "repo_full_name": "example/project",
        "issue_number": 1,
        "issue_title": "Example bug",
        "issue_body": "The application crashes on invalid input.",
        "stage": "intake",
        "problem_summary": "",
        "likely_files": [],
        "plan": [],
        "repair_attempts": 0,
        "max_repair_attempts": 3,
        "status": "planning",
        "error": None,
    }


def test_graph_stops_after_planning(monkeypatch):
    def fake_analysis(**kwargs):
        assert kwargs["repo_full_name"] == "example/project"
        assert kwargs["issue_number"] == 1

        return IssueAnalysis(
            problem_summary="Invalid input causes a crash.",
            investigation_steps=[
                "Inspect input validation.",
                "Review related tests.",
            ],
            needs_more_information=False,
            questions_for_reporter=[],
        )

    monkeypatch.setattr(
        graph_module,
        "analyze_issue_with_gemini",
        fake_analysis,
    )

    result = graph_module.graph.invoke(make_initial_state())

    assert result["problem_summary"] == (
        "Invalid input causes a crash."
    )
    assert len(result["plan"]) == 2
    assert result["status"] == "awaiting_start"
    assert result["repair_attempts"] == 0


def test_graph_handles_llm_failure(monkeypatch):
    def failing_analysis(**kwargs):
        raise RuntimeError("Simulated provider failure")

    monkeypatch.setattr(
        graph_module,
        "analyze_issue_with_gemini",
        failing_analysis,
    )

    result = graph_module.graph.invoke(make_initial_state())

    assert result["status"] == "failed"
    assert result["stage"] == "plan"
    assert "Simulated provider failure" in result["error"]
