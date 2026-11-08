"""Adapter from the LangGraph prototype (`agent/graph.py`) to `AgentRunner`.

Planning uses the real graph; repair execution uses the Docker-verified runner.
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
            "base_sha": task.base_sha,
            "repo_full_name": task.repo_full_name,
            "issue_number": task.issue.get("number"),
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
        from app.schemas.enums import EventType, TaskStage
        ctx.stage(TaskStage.PLAN)
        ctx.event(EventType.INFO, "Planning started. Acquiring repository context before calling the model.")
        from app.agent.attachment_context import attachment_context
        state["issue_body"] += attachment_context(task, ctx)
        current = ctx._svc.store.get(f"tasks/{task.task_id}")["budgets"]
        with ctx.repository() as repo_path:
            state["repo_path"] = str(repo_path) if repo_path else None
            if repo_path:
                from app.agent.cleanup_runner import cleanup_plan
                cleanup = cleanup_plan(task,ctx,repo_path)
                if cleanup is not None: return cleanup
                from app.agent.repair_runner import repository_context
                state["issue_body"] += "\n\nUNTRUSTED REPOSITORY CONTEXT\n" + repository_context(Path(repo_path), 30000)
            ctx.event(EventType.LLM_CALL, "Requesting an investigation plan from the configured model provider.")
            from agent.router import get_router, ModelRouter, router_scope
            base = get_router()
            def reserve_call(provider, model):
                ctx.checkpoint()
                budgets = ctx._svc.store.get(f"tasks/{task.task_id}")["budgets"]
                ctx.usage(modelCalls=budgets["modelCalls"] + 1)
                ctx.event(EventType.LLM_CALL, f"Planning with {provider}/{model}.")
            active = ModelRouter(base.settings, provider_factory=base.get_provider, before_call=reserve_call, health=base.health, on_transition=lambda detail: ctx.event(EventType.INFO, detail["message"]))
            with router_scope(active):
                result = self._graph.invoke(state)
            ctx.checkpoint()
            usage = result.get("token_usage") or {}
            ctx.usage(inputTokens=current["inputTokens"] + (usage.get("input_tokens") or 0), outputTokens=current["outputTokens"] + (usage.get("output_tokens") or 0))
            ctx.event(EventType.INFO, "Planning model returned a response.")
        if result["status"] == "failed":
            return PlanResult(
                "infra_retry",
                failure_code=FailureCode.LLM_UNAVAILABLE,
                failure_message=result.get("error") or "Issue analysis failed.",
            )
        return PlanResult(
            "needs_input" if result.get("needs_more_information") else "ready",
            plan=PlanData(
                title=result.get("task_title", ""),
                problem_summary=result["problem_summary"],
                steps=result["plan"],
                likely_files=result.get("likely_files", []),
                questions=result.get("questions_for_reporter", []),
            ),
        )

    def execute(self, task: AgentTask, ctx: JobContext) -> ExecuteResult:
        from app.agent.repair_runner import run_repair
        return run_repair(task, ctx)
