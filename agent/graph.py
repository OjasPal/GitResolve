
from langgraph.graph import END, START, StateGraph

from agent.llm import analyze_issue_with_gemini
from agent.state import AgentState


def analyze_issue(state: AgentState) -> dict:
    """Validate the issue input before calling the LLM."""

    if not state["repo_full_name"].strip():
        raise ValueError("Repository name cannot be empty.")

    if state["issue_number"] <= 0:
        raise ValueError("Issue number must be positive.")

    if not state["issue_title"].strip():
        raise ValueError("Issue title cannot be empty.")

    return {
        "stage": "plan",
        "status": "planning",
        "error": None,
    }


def create_plan(state: AgentState) -> dict:
    """Generate an issue-specific investigation plan with Gemini."""

    try:
        analysis = analyze_issue_with_gemini(
            repo_full_name=state["repo_full_name"],
            issue_number=state["issue_number"],
            issue_title=state["issue_title"],
            issue_body=state["issue_body"],
        )

    except Exception as exc:
        return {
            "stage": "plan",
            "status": "failed",
            "error": (
                f"Issue analysis failed "
                f"({type(exc).__name__}): {exc}"
            ),
        }

    return {
        "problem_summary": analysis.problem_summary,
        "plan": analysis.investigation_steps,
        "needs_more_information": analysis.needs_more_information,
        "questions_for_reporter": analysis.questions_for_reporter,
        "stage": "awaiting_start",
        "status": "awaiting_start",
        "error": None,
    }


def await_start(state: AgentState) -> dict:
    """End the planning workflow while waiting for a future approval flow."""

    return {
        "stage": "awaiting_start",
        "status": "awaiting_start",
    }


def route_after_planning(state: AgentState) -> str:
    """Stop if analysis failed; otherwise finish the planning flow."""

    if state["status"] == "failed":
        return "end"

    return "await_start"


def build_graph():
    builder = StateGraph(AgentState)

    builder.add_node("analyze_issue", analyze_issue)
    builder.add_node("create_plan", create_plan)
    builder.add_node("await_start", await_start)

    builder.add_edge(START, "analyze_issue")
    builder.add_edge("analyze_issue", "create_plan")

    builder.add_conditional_edges(
        "create_plan",
        route_after_planning,
        {
            "end": END,
            "await_start": "await_start",
        },
    )

    builder.add_edge("await_start", END)

    return builder.compile()


graph = build_graph()
