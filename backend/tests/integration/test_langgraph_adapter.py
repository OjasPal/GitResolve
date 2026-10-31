"""Plan phase through the real LangGraph (LLM mocked, as in agent/test_graph.py)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("google.genai")

import agent.graph as graph_module  # noqa: E402  (root-level package, see pyproject pythonpath)
from agent.analysis_schema import IssueAnalysis  # noqa: E402

from app.agent.langgraph_adapter import LangGraphAgentRunner  # noqa: E402
from tests.conftest import API  # noqa: E402


def test_langgraph_adapter_loads_when_started_from_backend_directory():
    backend_dir = Path(__file__).resolve().parents[2]
    script = (
        "from pathlib import Path; "
        "from app.agent.langgraph_adapter import LangGraphAgentRunner; "
        "LangGraphAgentRunner(); "
        "import agent.graph; "
        "assert Path(agent.graph.__file__).is_file()"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=backend_dir,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_plan_phase_runs_the_real_graph(env, monkeypatch):
    def fake_analysis(**kwargs):
        assert kwargs["issue_number"] == 1 and kwargs["repo_full_name"] == "demo/seeded-bugs"
        return IssueAnalysis(
            problem_summary="Empty input crashes the parser.",
            investigation_steps=["Inspect parse_duration.", "Add a regression test."],
            needs_more_information=False,
            questions_for_reporter=[],
        )

    monkeypatch.setattr(graph_module, "analyze_issue_with_gemini", fake_analysis)
    env.worker.agent = LangGraphAgentRunner()
    h = env.headers()
    tid = env.create(h).json()["id"]
    env.run_worker()
    task = env.get(h, tid)
    assert task["status"] == "awaiting_start"
    assert task["plan"]["problemSummary"] == "Empty input crashes the parser."
    assert task["plan"]["steps"] == ["Inspect parse_duration.", "Add a regression test."]


def test_llm_failure_and_missing_execute_are_reported_not_crashed(env, monkeypatch):
    def failing(**kwargs):
        raise RuntimeError("Simulated provider failure")

    monkeypatch.setattr(graph_module, "analyze_issue_with_gemini", failing)
    env.worker.agent = LangGraphAgentRunner()
    h = env.headers()
    tid = env.create(h).json()["id"]
    env.run_worker()
    task = env.get(h, tid)
    assert task["status"] == "failed_infra" and task["failure"]["code"] == "llm_unavailable"
    assert "Simulated provider failure" in task["failure"]["message"]

    monkeypatch.setattr(graph_module, "analyze_issue_with_gemini", lambda **kw: IssueAnalysis(
        problem_summary="s", investigation_steps=["x"], needs_more_information=False, questions_for_reporter=[]))
    tid = env.create(h, 2).json()["id"]
    env.run_worker()
    env.post(h, tid, "start")
    env.run_worker()  # execute() is not implemented in the prototype yet
    task = env.get(h, tid)
    assert task["status"] == "failed_infra" and task["failure"]["code"] == "internal_error"
    assert task["failure"]["phase"] == "execute"
