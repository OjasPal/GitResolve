"""Adapter from the LangGraph prototype (`agent/graph.py`) to `AgentRunner`.

The prototype currently implements real Gemini-backed planning only. Repair
execution remains unimplemented.
"""

import sys
from pathlib import Path

from app.agent.interface import AgentTask, ExecuteResult, PlanResult
from app.schemas.enums import FailureCode
from app.schemas.tasks import PlanData
from app.worker.context import JobContext


class LangGraphAgentRunner:
    def __init__(self) -> None:
        backend_root = next(
            (
                parent
                for parent in Path(__file__).resolve().parents
                if (parent / "app" / "main.py").is_file()
            ),
            None,
        )
        if backend_root is None:
            raise RuntimeError("Could not locate the backend project root.")

        from dotenv import load_dotenv

        load_dotenv(backend_root / ".env", override=False)

        agent_root = next(
            (
                parent / "agent"
                for parent in Path(__file__).resolve().parents
                if (parent / "agent" / "graph.py").is_file()
            ),
            None,
        )
        if agent_root is None:
            raise RuntimeError("Could not locate the project-level agent package.")
        project_root = str(agent_root.parent)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        from agent import graph as graph_module

        self._graph = graph_module.graph

    def plan(self, task: AgentTask, ctx: JobContext) -> PlanResult:
        ctx.checkpoint()
        state = {
            "task_id": task.task_id,
            "repo_full_name": task.repo_full_name,
            "issue_number": int(task.issue["number"]),
            "issue_title": task.issue.get("title") or "",
            "issue_body": task.issue.get("body") or "",
            "stage": "intake",
            "problem_summary": "",
            "likely_files": [],
            "plan": [],
            "repair_attempts": 0,
            "max_repair_attempts": int(task.limits["maxRepairAttempts"]),
            "status": "planning",
            "error": None,
        }
        ctx.usage(modelCalls=1)
        result = self._graph.invoke(state)
        if result["status"] == "failed":
            return PlanResult(
                "failed",
                failure_code=FailureCode.LLM_UNAVAILABLE,
                failure_message=result.get("error") or "Issue analysis failed.",
            )
        return PlanResult(
            "needs_input" if result.get("needs_more_information") else "ready",
            plan=PlanData(
                problem_summary=result["problem_summary"],
                steps=result["plan"],
                likely_files=result.get("likely_files", []),
                questions=result.get("questions_for_reporter", []),
            ),
        )

    def execute(self, task: AgentTask, ctx: JobContext) -> ExecuteResult:
        raise NotImplementedError("Repair execution is not implemented in the agent yet.")
