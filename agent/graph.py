import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any

from langgraph.graph import END, START, StateGraph

from agent.llm import analyze_issue_with_gemini
from agent.state import AgentState


def compute_plan_hash(plan_payload: dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 hash over the immutable plan fields."""
    canonical = {
        "task_type": str(plan_payload.get("task_type") or plan_payload.get("taskType") or "bug"),
        "problem_summary": str(plan_payload.get("problem_summary") or plan_payload.get("problemSummary") or ""),
        "steps": list(plan_payload.get("steps") or plan_payload.get("plan") or []),
        "spec": str(plan_payload.get("spec") or ""),
        "acceptance_criteria": list(
            plan_payload.get("acceptance_criteria") or plan_payload.get("acceptanceCriteria") or []
        ),
        "expected_files": list(
            plan_payload.get("expected_files") or plan_payload.get("expectedFiles") or []
        ),
        "line_budget": int(plan_payload.get("line_budget") or plan_payload.get("lineBudget") or 120),
        "dependency_changes": list(
            plan_payload.get("dependency_changes") or plan_payload.get("dependencyChanges") or []
        ),
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _extract_issue_files(issue_text: str) -> list[str]:
    clean = (issue_text or "").split("UNTRUSTED REPOSITORY CONTEXT", maxsplit=1)[0]
    found: list[str] = []
    for m in re.finditer(
        r"(?:^|[\s`'\"(])([A-Za-z0-9_./-]+\.py|\.flake8|\.gitignore|\.editorconfig|\.python-version|pytest\.ini|ruff\.toml|\.ruff\.toml|mypy\.ini|\.mypy\.ini|\.coveragerc|requirements[A-Za-z0-9_.-]*\.txt)(?:$|[\s`'\"),.:;])",
        clean,
    ):
        cand = m.group(1).replace("\\", "/").lstrip("./")
        if cand and cand not in found:
            found.append(cand)
    return found[:10]


def _classify_and_refine_plan_task_type(
    issue_title: str,
    issue_body: str,
    llm_task_type: str | None,
    needs_more_info: bool,
    expected_files: list[str],
) -> str:
    clean_body = (issue_body or "").split("UNTRUSTED REPOSITORY CONTEXT", maxsplit=1)[0]
    combined = f"{issue_title}\n{clean_body}".strip()
    try:
        from app.agent.lanes.triage import classify_task_type
        _, floor, _ = classify_task_type(
            combined,
            plan={
                "task_type": llm_task_type,
                "needs_more_information": needs_more_info,
                "expected_files": expected_files,
            },
        )
        return floor.value
    except Exception:
        caution = {"bug": 0, "refactor": 1, "config": 1, "feature": 2, "unclear": 3}
        det = "unclear" if needs_more_info else "bug"
        prop = (llm_task_type or "bug").strip().lower()
        if prop not in caution:
            prop = "bug"
        return prop if caution[prop] >= caution[det] else det


def analyze_issue(state: AgentState) -> dict:
    """Validate the issue input before calling the LLM."""

    if not state.get("repo_full_name", "").strip():
        raise ValueError("Repository name cannot be empty.")

    if state.get("issue_number", 0) is not None and state.get("issue_number", 0) <= 0:
        raise ValueError("Issue number must be positive.")

    if not state.get("issue_title", "").strip():
        raise ValueError("Issue title cannot be empty.")

    return {
        "stage": "plan",
        "status": "planning",
        "error": None,
    }


def create_plan(state: AgentState) -> dict:
    """Generate an issue-specific investigation plan using the routing architecture."""

    try:
        # Call analyze_issue_with_gemini (which may be monkeypatched by tests or invoke the central router)
        kwargs: dict[str, Any] = {
            "repo_full_name": state["repo_full_name"],
            "issue_number": state["issue_number"],
            "issue_title": state["issue_title"],
            "issue_body": state["issue_body"],
        }
        # Inspect compatibility before dispatch; never retry a call because its
        # implementation raised TypeError after a request was already sent.
        import inspect
        params = inspect.signature(analyze_issue_with_gemini).parameters
        if "task_id" in params or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
            kwargs["task_id"] = state.get("task_id")
        analysis = analyze_issue_with_gemini(**kwargs)

        router_result = getattr(analysis, "_router_result", None)
        router_meta = router_result.to_metadata() if router_result else {}

    except Exception as exc:
        from agent.observability import sanitize_error
        return {
            "stage": "plan",
            "status": "failed",
            "error": sanitize_error(exc),
        }

    raw_expected = list(getattr(analysis, "expected_files", None) or state.get("likely_files") or [])
    effective_type = _classify_and_refine_plan_task_type(
        state.get("issue_title", ""),
        state.get("issue_body", ""),
        getattr(analysis, "task_type", "bug"),
        bool(analysis.needs_more_information),
        raw_expected,
    )
    needs_more = bool(analysis.needs_more_information) or (effective_type == "unclear")
    questions = list(analysis.questions_for_reporter or [])
    if needs_more and not questions:
        questions = [
            "Could you clarify the target module, expected behavior, and concrete reproduction or acceptance steps?"
        ]

    response_state: dict[str, Any] = {
        "problem_summary": analysis.problem_summary,
        "task_title": analysis.task_title,
        "plan": analysis.investigation_steps,
        "needs_more_information": needs_more,
        "questions_for_reporter": questions,
        "task_type": effective_type,
        "spec": getattr(analysis, "spec", "") or "",
        "acceptance_criteria": list(getattr(analysis, "acceptance_criteria", None) or []),
        "expected_files": raw_expected,
        "line_budget": getattr(analysis, "line_budget", None),
        "dependency_changes": list(getattr(analysis, "dependency_changes", None) or []),
        "stage": "plan",
        "status": "planning",
        "error": None,
    }

    if router_meta:
        response_state.update(router_meta)
        response_state["routing_metadata"] = router_meta

    return response_state


def spec_node(state: AgentState) -> dict:
    """Derive or normalize the immutable task specification in the PLAN subgraph."""
    raw_spec = (state.get("spec") or "").strip()
    if not raw_spec:
        summary = (state.get("problem_summary") or "").strip()
        title = (state.get("task_title") or state.get("issue_title") or "").strip()
        task_type = state.get("task_type") or "bug"
        raw_spec = f"[{task_type}] {title}: {summary}".strip(": ")
    return {"spec": raw_spec[:2000]}


def acceptance_criteria_node(state: AgentState) -> dict:
    """Derive or normalize mode-aware acceptance criteria in the PLAN subgraph."""
    existing = [str(c).strip() for c in (state.get("acceptance_criteria") or []) if str(c).strip()]
    if existing:
        return {"acceptance_criteria": existing[:10]}

    task_type = (state.get("task_type") or "bug").strip().lower()
    steps = [str(s).strip() for s in (state.get("plan") or []) if str(s).strip()]
    criteria: list[str] = []
    if task_type == "feature":
        criteria.append("Acceptance tests derived from the approved spec fail on the original codebase and pass after implementation.")
        criteria.append("Existing repository test suite remains green with zero new failures.")
    elif task_type == "refactor":
        criteria.append("Behavior-preserving characterization tests pass both before and after the refactoring.")
        criteria.append("All touched symbols have verified test coverage and public exports remain unchanged unless explicitly approved.")
    elif task_type == "config":
        criteria.append("Only allowlisted configuration profile files are modified and pass deterministic syntax validation.")
        criteria.append("Existing repository test suite remains green with zero new failures.")
    else:
        criteria.append("Regression test reproduces the failure before the fix and passes after the fix.")
        criteria.append("Existing repository test suite remains green with zero new failures.")

    for step in steps[:3]:
        if step not in criteria:
            criteria.append(step)
    return {"acceptance_criteria": criteria[:10]}


def file_plan_node(state: AgentState) -> dict:
    """Finalize expected files, line budget, dependency changes, and immutable plan hash."""
    expected = [
        str(f).replace("\\", "/").lstrip("./")
        for f in (state.get("expected_files") or state.get("likely_files") or [])
        if str(f).strip()
    ]
    if not expected:
        expected = _extract_issue_files(
            f"{state.get('issue_title', '')}\n{state.get('issue_body', '')}"
        )
    likely = list(state.get("likely_files") or expected)
    raw_budget = state.get("line_budget")
    line_budget = int(raw_budget) if isinstance(raw_budget, int) and raw_budget > 0 else 120
    dep_changes = list(state.get("dependency_changes") or [])

    plan_hash = compute_plan_hash({
        "task_type": state.get("task_type") or "bug",
        "problem_summary": state.get("problem_summary") or "",
        "steps": state.get("plan") or [],
        "spec": state.get("spec") or "",
        "acceptance_criteria": state.get("acceptance_criteria") or [],
        "expected_files": expected,
        "line_budget": line_budget,
        "dependency_changes": dep_changes,
    })
    return {
        "expected_files": expected,
        "likely_files": likely,
        "line_budget": line_budget,
        "dependency_changes": dep_changes,
        "plan_hash": plan_hash,
    }


def await_start(state: AgentState) -> dict:
    """End the planning workflow while waiting for backend human plan approval."""

    return {
        "stage": "awaiting_start",
        "status": "awaiting_start",
    }


def route_after_planning(state: AgentState) -> str:
    """Stop if analysis failed; otherwise run spec -> acceptance_criteria -> file_plan -> await_start."""

    if state.get("status") == "failed":
        return "end"

    return "spec"


def build_graph():
    builder = StateGraph(AgentState)

    builder.add_node("analyze_issue", analyze_issue)
    builder.add_node("create_plan", create_plan)
    builder.add_node("spec", spec_node)
    builder.add_node("acceptance_criteria", acceptance_criteria_node)
    builder.add_node("file_plan", file_plan_node)
    builder.add_node("await_start", await_start)

    builder.add_edge(START, "analyze_issue")
    builder.add_edge("analyze_issue", "create_plan")

    builder.add_conditional_edges(
        "create_plan",
        route_after_planning,
        {
            "end": END,
            "spec": "spec",
        },
    )

    builder.add_edge("spec", "acceptance_criteria")
    builder.add_edge("acceptance_criteria", "file_plan")
    builder.add_edge("file_plan", "await_start")
    builder.add_edge("await_start", END)

    return builder.compile()


graph = build_graph()
