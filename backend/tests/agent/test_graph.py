import agent.graph as graph_module
from agent.analysis_schema import IssueAnalysis
from agent.config import RouterSettings
from agent.observability import create_usage_record
from agent.providers import BaseProvider, ModelResult, RetryableProviderError
from agent.router import ModelRouter, set_global_router


class MockProvider(BaseProvider):
    def __init__(self, name: str):
        self.name = name
        self.calls = []
        self.queued_responses = {}

    def queue(self, model_id: str, res_or_exc):
        self.queued_responses.setdefault(model_id, []).append(res_or_exc)

    def generate(self, model_id: str, prompt: str, schema=None, system_instruction=None, task_id=None):
        self.calls.append(model_id)
        queue = self.queued_responses.get(model_id, [])
        if queue:
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return ModelResult(
            provider=self.name,
            model_id=model_id,
            parsed=IssueAnalysis(
                problem_summary="Standard analysis",
                investigation_steps=["Step 1"],
                needs_more_information=False,
                questions_for_reporter=[],
            ),
            usage=create_usage_record(self.name, model_id, 200, 100),
        )


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


def test_graph_records_routing_metadata():
    google = MockProvider("google")
    settings = RouterSettings(max_provider_retries=1)

    router = ModelRouter(
        settings=settings,
        provider_factory=lambda n: google,
        sleeper=lambda d: None,
    )
    set_global_router(router)
    try:
        result = graph_module.graph.invoke(make_initial_state())
        assert result["status"] == "awaiting_start"
        assert result["selected_provider"] == "google"
        assert result["selected_model"] == "gemini-3.8-flash"
        assert result["failover_occurred"] is False
        assert result["specialist_escalation_occurred"] is False
        assert result["routing_metadata"]["selected_model"] == "gemini-3.8-flash"
        assert result["token_usage"] is not None
        assert result["token_usage"]["input_tokens"] == 200
    finally:
        set_global_router(None)


def test_graph_records_failover_metadata_in_state():
    google = MockProvider("google")

    # Argon fails twice with transient 503
    for _ in range(2):
        google.queue("gemini-3.8-flash", RetryableProviderError("503", "google", "gemini-3.8-flash", 503))

    settings = RouterSettings(max_provider_retries=1)
    router = ModelRouter(
        settings=settings,
        provider_factory=lambda n: google,
        sleeper=lambda d: None,
    )
    set_global_router(router)
    try:
        result = graph_module.graph.invoke(make_initial_state())
        assert result["status"] == "awaiting_start"
        assert result["selected_provider"] == "google"
        assert result["selected_model"] == settings.fallback_1.model_id
        assert result["failover_occurred"] is True
        assert result["specialist_escalation_occurred"] is False
        assert "Technical failover" in result["model_switch_reason"]
    finally:
        set_global_router(None)


def test_planning_type_error_does_not_duplicate_a_dispatched_call(monkeypatch):
    calls=[]
    def analysis(**kwargs):
        calls.append(kwargs)
        raise TypeError("Internal response processing failure")
    monkeypatch.setattr(graph_module,"analyze_issue_with_gemini",analysis)
    result=graph_module.graph.invoke(make_initial_state())
    assert result["status"]=="failed" and len(calls)==1
