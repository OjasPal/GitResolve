"""Frontend-free planning demonstration: python -m app.demo [--mock-llm]."""

import argparse
import json
import time
from contextlib import nullcontext
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def run_demo(mock_llm: bool = False) -> dict:
    # Explicitly uses the real graph; only its provider call can be mocked.
    from app.agent.langgraph_adapter import LangGraphAgentRunner
    LangGraphAgentRunner()  # establish the monorepo import path
    from agent.analysis_schema import IssueAnalysis
    analysis = IssueAnalysis(problem_summary="Empty input requires investigation.",
                             investigation_steps=["Inspect parser and regression tests."],
                             needs_more_information=False, questions_for_reporter=[])
    provider = patch("agent.graph.analyze_issue_with_gemini", return_value=analysis) if mock_llm else nullcontext()
    settings = Settings(environment="local", store_backend="memory", agent_mode="langgraph",
                        embedded_worker=True, poll_interval_seconds=1, _env_file=None)
    with provider, TestClient(create_app(settings=settings)) as client:
        token = client.post("/api/v1/auth/dev-token", json={"userId": "1", "githubLogin": "demo"}).json()["accessToken"]
        headers = {"Authorization": f"Bearer {token}"}
        response = client.post("/api/v1/tasks", headers=headers, json={"repoId": "1001_2002", "issueNumber": 1})
        response.raise_for_status()
        task_id = response.json()["id"]
        deadline = time.monotonic() + (10 if mock_llm else 90)
        while time.monotonic() < deadline:
            task = client.get(f"/api/v1/tasks/{task_id}", headers=headers).json()
            if task["status"] not in {"queued", "planning"}:
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("Demo did not finish within its bounded wait.")
        events = client.get(f"/api/v1/tasks/{task_id}/events", headers=headers).json()["items"]
        artifacts = client.get(f"/api/v1/tasks/{task_id}/artifacts", headers=headers).json()["items"]
        return {"submitted": response.status_code, "status": task["status"], "plan": task["plan"],
                "events": len(events), "artifacts": [a["id"] for a in artifacts],
                "llm_mocked": mock_llm, "github_gateway": "local fixture", "pr_created": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mock-llm", action="store_true")
    print(json.dumps(run_demo(parser.parse_args().mock_llm), indent=2))
