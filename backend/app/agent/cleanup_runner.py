"""Approved test-file maintenance, verified without asking a model to invent a bug."""
from pathlib import Path
import re
from sandbox.cleanup import build_scope, validate_scope, deletion_patch, validate_cleanup_patch, references
from app.agent.interface import PlanResult, ExecuteResult
from app.schemas.tasks import PlanData
from app.schemas.enums import TaskStage, EventType, ArtifactType, FailureCode


def cleanup_plan(task,ctx,root):
    try:
        scope=build_scope(root,task.issue.get("body") or "",task.base_sha)
    except (ValueError,SyntaxError,UnicodeError) as exc:
        return PlanResult("needs_input",plan=PlanData(title="Review root test cleanup",problem_summary="Cleanup needs clarification before files can be removed.",steps=["Resolve the inspection finding before approving a deletion."],questions=[str(exc)[:1000]]))
    if scope is None: return None
    names=[f["path"] for f in scope["files"]]
    ctx.stage(TaskStage.PLAN,cleanupScope=scope)
    ctx.event(EventType.INFO,"Explicit root-test cleanup identified. Exact file fingerprints and a task-scoped removal budget are recorded for plan approval.")
    return PlanResult("ready",plan=PlanData(title="Safely remove unused root test files",
        problem_summary=f"Remove exactly {len(names)} root test files after checking application/configuration references. Application source will remain unchanged.",
        likely_files=names,steps=[
            "Approve removal of only: "+", ".join(names)+".",
            f"Review the scoped limit: {sum(f['lines'] for f in scope['files'])} removed lines; maximum {scope['maxDiffLines']} changed lines for this task only.",
            "Run the existing tests in isolated Docker before cleanup. Stop if the baseline does not pass.",
            "Delete the approved files, then rerun their immutable original automated tests against the cleaned workspace. Verify no application source changed and no references to removed modules remain.",
            "Review the exact deletion diff and compatibility evidence before approving a draft PR. No test replacements will be published."
        ]))


def run_cleanup(task,ctx):
    from app.agent.repair_runner import passed,output,streamlit_entrypoint
    if task.attempts_used >= task.limits["maxRepairAttempts"]:
        return ExecuteResult("failed",failure_code=FailureCode.BUDGET_EXCEEDED,failure_message="This run has reached its repair-attempt limit.")
    scope=task.cleanup_scope
    try:
        stored=ctx._svc.store.get(f"tasks/{task.task_id}")
        names=validate_scope(scope,task.base_sha,stored["issue"].get("body") or "")
        with ctx.repository() as root:
            if root is None: raise ValueError("A pinned source snapshot is required.")
            if references(root,names): raise ValueError("Application references require a new investigation.")
            patch=deletion_patch(root,scope)
            validate_cleanup_patch(patch,scope)
            originals={p.relative_to(root).as_posix():p.read_text(encoding="utf-8-sig") for p in Path(root).rglob("*.py") if p.is_file() and not p.is_symlink()}
            entry=streamlit_entrypoint(originals,"")
        if entry:
            ctx.sandbox_profile="streamlit";ctx.ui_entrypoint=entry
        command=["python","-m","pytest","-q","--tb=short"]
        ctx.stage(TaskStage.BASELINE,testCommand=command)
        ctx.event(EventType.TEST_RUN,"Running the original test suite before cleanup; existing failures will not be hidden by deleting tests.")
        baseline=ctx.execute(commit_sha=task.base_sha,command=command)
        baseline_tests=sum(int(n) for n in re.findall(r"\b(\d+) passed\b",baseline.stdout))
        if not passed(baseline) or not baseline_tests:
            ctx.artifact("final_tests",ArtifactType.FINAL_TESTS,output(baseline))
            return ExecuteResult("failed",failure_code=FailureCode.SANDBOX_ERROR,failure_message="The existing tests did not pass before cleanup. Repair the baseline before removing test files.")
        ctx.stage(TaskStage.REPRODUCE,reproduction={"kind":"cleanup_preconditions","targetsPresent":True,"paths":names,"applicationReferences":[]})
        ctx.usage(attempts_used=task.attempts_used+1)
        ctx.stage(TaskStage.PATCH)
        ctx.event(EventType.TOOL_CALL,"Removing only the approved root test files inside Docker: "+", ".join(names))
        ctx.artifact(f"attempt_{task.attempts_used+1:03d}_diff",ArtifactType.ATTEMPT_DIFF,patch,task.attempts_used+1)
        ctx.stage(TaskStage.TEST)
        final=ctx.execute(commit_sha=task.base_sha,command=command,patch=patch)
        transcript="ORIGINAL TEST SUITE\n"+output(baseline)+"\nCLEANED WORKSPACE — IMMUTABLE ORIGINAL TESTS\n"+output(final)
        ctx.artifact("final_tests",ArtifactType.FINAL_TESTS,transcript)
        tests=sum(int(n) for n in re.findall(r"\b(\d+) passed\b",final.stdout))
        if not passed(final) or tests < baseline_tests or "CLEANUP GATE PASSED:" not in final.stdout:
            return ExecuteResult("failed",failure_code=FailureCode.SANDBOX_ERROR,failure_message="Cleanup compatibility checks did not pass. No deletion patch has been approved.")
        ctx.stage(TaskStage.EVALUATE)
        ctx.event(EventType.POLICY_CHECK,"Cleanup compatibility verified: original tests passed against the cleaned workspace; application source is unchanged and all approved files are absent.")
        ctx.stage(TaskStage.POLICY_GATE)
        return ExecuteResult("fix_ready",final_diff=patch,final_tests=transcript,
            summary={"filesChanged":len(names),"linesAdded":0,"linesRemoved":sum(f["lines"] for f in scope["files"]),
                "commitMessage":"chore: Remove unused root test files","branchName":"remove-root-tests","verificationKind":"cleanup",
                "uiVerification":"Original compatibility tests rerun; application source unchanged. No new UI changes."},
            verification={"reproduced":True,"regressionPassed":True,"testsRun":tests,"newFailures":[]})
    except (ValueError,SyntaxError,UnicodeError) as exc:
        ctx.event(EventType.POLICY_CHECK,"Cleanup stopped before publication: "+str(exc)[:500])
        return ExecuteResult("failed",failure_code=FailureCode.POLICY_VIOLATION,failure_message="Cleanup requires a new investigation: "+str(exc)[:300])
