"""Scripted agent for exercising the full task lifecycle without an LLM or sandbox."""

from app.agent.interface import AgentTask, ExecuteResult, PlanResult
from app.schemas.enums import EventType, FailureCode, TaskStage
from app.schemas.tasks import PlanData
from app.worker.context import JobContext

SCENARIOS = {"success", "needs_input", "plan_failed", "not_reproduced", "no_fix"}

_DIFF = """--- a/src/slugify.py
+++ b/src/slugify.py
@@ -1,2 +1,2 @@
 def slugify(text):
-    return re.sub(r"[^a-z0-9]+", "-", text.lower())
+    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
--- /dev/null
+++ b/tests/test_slugify_regression.py
@@ -0,0 +1,3 @@
+from src.slugify import slugify
+def test_trailing_hyphen():
+    assert slugify('Hello!') == 'hello'
"""


class FakeAgentRunner:
    def __init__(self, scenario: str = "success") -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"Unknown scenario: {scenario}")
        self.scenario = scenario

    def plan(self, task: AgentTask, ctx: JobContext) -> PlanResult:
        ctx.checkpoint()
        ctx.usage(modelCalls=1, inputTokens=0, outputTokens=0)
        if self.scenario == "plan_failed":
            return PlanResult("failed", failure_code=FailureCode.LLM_UNAVAILABLE, failure_message="Simulated LLM outage.")
        plan = PlanData(
            problem_summary=f"Investigate: {task.issue.get('title')}",
            steps=["Locate the relevant function.", "Write a failing regression test.", "Patch and re-run tests."],
            likely_files=["src/slugify.py"],
            questions=["Which input triggers the bug?"] if self.scenario == "needs_input" else [],
        )
        if self.scenario == "needs_input":
            return PlanResult("needs_input", plan=plan)
        return PlanResult("ready", plan=plan)

    def execute(self, task: AgentTask, ctx: JobContext) -> ExecuteResult:
        ctx.stage(TaskStage.SETUP, baseSha="a" * 40)
        ctx.stage(TaskStage.BASELINE, baseline={"ran": 12, "failed": 0})
        ctx.stage(TaskStage.LOCATE)
        ctx.stage(TaskStage.REPRODUCE, reproduction={"outcome": "reproduced"})
        if self.scenario == "not_reproduced":
            return ExecuteResult("failed", failure_code=FailureCode.NOT_REPRODUCED, failure_message="Could not reproduce the bug.")
        ctx.stage(TaskStage.PATCH)
        ctx.event(EventType.ATTEMPT, "Attempt 1 started.", data={"n": 1})
        ctx.stage(TaskStage.TEST)
        ctx.usage(attempts_used=1, modelCalls=4)
        if self.scenario == "no_fix":
            return ExecuteResult("failed", failure_code=FailureCode.NO_FIX_FOUND, failure_message="No passing fix within the attempt limit.")
        ctx.stage(TaskStage.EVALUATE)
        ctx.stage(TaskStage.POLICY_GATE)
        return ExecuteResult(
            "fix_ready",
            final_diff=_DIFF,
            final_tests="before: 1 failed, 12 passed\nafter: 0 failed, 13 passed\n",
            verification={"reproduced": True, "regressionPassed": True, "testsRun": 13, "newFailures": []},
            summary={"filesChanged": 2, "linesAdded": 4, "linesRemoved": 1,
                     "files": ["src/slugify.py", "tests/test_slugify_regression.py"]},
        )
