from contextlib import contextmanager
from types import SimpleNamespace
import subprocess

import pytest

from app.agent.repair_runner import EditProposal, RepairProposal, QualityReview, compile_edits, repository_context, run_repair, validate_proposal
from app.schemas.enums import FailureCode


TEST = """diff --git a/test_math.py b/test_math.py
new file mode 100644
--- /dev/null
+++ b/test_math.py
@@ -0,0 +1,3 @@
+from math_example import add
+def test_add():
+    assert add(1, 2) == 3
"""
FIX = """diff --git a/math_example.py b/math_example.py
--- a/math_example.py
+++ b/math_example.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
"""
LIMITS = {"maxRepairAttempts": 1, "maxDiffFiles": 5, "maxDiffLines": 150}


def approved_review():
    return QualityReview(commit_message="fix: Correct addition results", approved=True,
                         requirements=[{"requirement": "Correct addition", "status": "passed", "evidence": "Regression fails before and passes after."}], blocking_issues=[])


def result(code, stdout):
    return SimpleNamespace(status="passed" if code == 0 else "failed", exit_code=code,
                           stdout=stdout, stderr="", oom_killed=False, cleanup_ok=True)


class Context:
    def __init__(self, root, results):
        self.root, self.results, self.events, self.commands = root, iter(results), [], []
        self.budgets = {"modelCalls": 1, "inputTokens": 0, "outputTokens": 0}
        self._svc = SimpleNamespace(store=SimpleNamespace(get=lambda path: {"budgets": self.budgets.copy()}))

    def checkpoint(self): pass
    def usage(self, attempts_used=None, **kw): self.budgets.update(kw)
    def stage(self, stage, **kw): self.events.append(stage)
    def event(self, kind, message): self.events.append(message)
    def artifact(self, *args): pass

    @contextmanager
    def repository(self): yield self.root

    def execute(self, **kw):
        self.commands.append(kw)
        return next(self.results)


def run(tmp_path, results, review=None):
    (tmp_path / "math_example.py").write_text("def add(a, b):\n    return a - b\n")
    proposal = RepairProposal(explanation="Fix addition", reproduction_diff=TEST, final_diff=TEST + FIX)
    router = SimpleNamespace(route_normal=lambda *a, **k: SimpleNamespace(
        result=SimpleNamespace(parsed=(review or approved_review()) if k.get("schema") is QualityReview else proposal), usage=None, selected_provider="test", selected_model="mock"))
    task = SimpleNamespace(base_sha="a" * 40, task_id="tsk_test", limits=LIMITS,
                           issue={"body": "Addition should add"}, plan={})
    ctx = Context(tmp_path, results)
    return run_repair(task, ctx, router), ctx


def test_repair_requires_before_failure_and_after_success(tmp_path):
    outcome, ctx = run(tmp_path, [result(0, "1 passed"), result(1, "1 failed"), result(0, "2 passed")])
    assert outcome.outcome == "fix_ready"
    assert outcome.final_diff == FIX
    assert outcome.summary["linesAdded"] == 1 and outcome.summary["linesRemoved"] == 1
    assert outcome.verification == {"reproduced": True, "regressionPassed": True, "testsRun": 2, "newFailures": []}
    assert [call.get("patch", "") for call in ctx.commands] == ["", TEST, TEST + FIX]
    assert ctx.commands[0]["command"] == ctx.commands[2]["command"] == ["python", "-m", "pytest", "-q", "--tb=short"]
    assert ctx.commands[1]["command"] == ["python", "-m", "pytest", "-q", "--tb=short", "--", "test_math.py"]


def test_passing_reproduction_cannot_be_claimed_as_a_fix(tmp_path):
    outcome, ctx = run(tmp_path, [result(0, "1 passed"), result(0, "2 passed")])
    assert outcome.outcome == "failed"
    assert len(ctx.commands) == 2


def test_failed_final_tests_cannot_be_claimed_as_a_fix(tmp_path):
    outcome, _ = run(tmp_path, [result(0, "1 passed"), result(1, "1 failed"), result(1, "1 failed")])
    assert outcome.outcome == "failed"


def test_missing_dependency_is_not_reproduction_evidence(tmp_path):
    outcome, ctx = run(tmp_path, [result(0, "1 passed"), result(1, "1 failed: ModuleNotFoundError")])
    assert outcome.outcome == "failed"
    assert len(ctx.commands) == 2


@pytest.mark.parametrize("status", ["failed", "not_verified"])
def test_passing_tests_do_not_override_unmet_requirements(tmp_path, status):
    review = approved_review()
    review.requirements[0].status = status
    outcome, ctx = run(tmp_path, [result(0, "1 passed"), result(1, "1 failed"), result(0, "2 passed")], review)
    assert outcome.outcome == "failed"
    assert outcome.final_diff == ""
    assert ctx.budgets["modelCalls"] == 3  # plan, implementation, independent review


def test_retry_receives_its_previous_proposal_and_test_failure(tmp_path):
    (tmp_path / "math_example.py").write_text("def add(a, b):\n    return a - b\n")
    proposal = RepairProposal(explanation="Fix addition", reproduction_diff=TEST, final_diff=TEST + FIX)
    prompts = []
    def route(prompt, **kwargs):
        prompts.append(prompt)
        return SimpleNamespace(result=SimpleNamespace(parsed=approved_review() if kwargs.get("schema") is QualityReview else proposal), usage=None,
                               selected_provider="test", selected_model="mock")
    task = SimpleNamespace(base_sha="a" * 40, task_id="tsk_test", limits={**LIMITS, "maxRepairAttempts": 2},
                           issue={"body": "Addition should add"}, plan={})
    ctx = Context(tmp_path, [result(0, "1 passed"), result(1, "1 failed"), result(1, "1 failed: first failure"),
                             result(1, "1 failed"), result(0, "2 passed")])
    assert run_repair(task, ctx, SimpleNamespace(route_normal=route)).outcome == "fix_ready"
    assert "first failure" in prompts[1]
    assert '"final_diff"' in prompts[1] and "Fix addition" in prompts[1]


def test_broken_baseline_stops_before_model_or_patch(tmp_path):
    outcome, ctx = run(tmp_path, [result(2, "ERROR collecting: missing dependency")])
    assert outcome.failure_code == FailureCode.SANDBOX_ERROR
    assert len(ctx.commands) == 1


def test_existing_missing_export_is_a_repair_target_not_infrastructure_failure(tmp_path):
    outcome, ctx = run(tmp_path, [result(2, "ImportError: cannot import name 'generate_mcqs_docx' from 'utils'"),
                                 result(1, "1 failed"), result(0, "2 passed")])
    assert outcome.outcome == "fix_ready"
    assert len(ctx.commands) == 3


def test_changed_regression_is_rejected():
    proposal = RepairProposal(explanation="bad", reproduction_diff=TEST, final_diff=TEST.replace("== 3", "== 4") + FIX)
    with pytest.raises(ValueError, match="identical"):
        validate_proposal(proposal, LIMITS, "a" * 40)


@pytest.mark.parametrize("fail_evaluation", [False, True])
def test_technical_recovery_preserves_evidence_and_attempt_budget(tmp_path, monkeypatch, fail_evaluation):
    from agent.config import RouterSettings
    from agent.providers import BaseProvider, ModelResult, RetryableProviderError
    from agent.router import ModelRouter
    (tmp_path / "math_example.py").write_text("def add(a, b):\n    return a - b\n")
    calls = []
    class Provider(BaseProvider):
        def generate(self, model_id, **kwargs):
            calls.append(model_id)
            if fail_evaluation and len(calls) == 1:
                return ModelResult("google", model_id, parsed=RepairProposal(explanation="Fix addition", reproduction_diff=TEST, final_diff=TEST+FIX))
            raise RetryableProviderError("deadline expired", "google", model_id, 504)
    router = ModelRouter(RouterSettings(), provider_factory=lambda _:Provider())
    monkeypatch.setattr("agent.router.get_router", lambda:router)
    class RecordingContext(Context):
        def __init__(self, *args):
            super().__init__(*args); self.attempts=[]; self.artifacts={}
        def usage(self, attempts_used=None, **kwargs):
            if attempts_used is not None: self.attempts.append(attempts_used)
            super().usage(attempts_used, **kwargs)
        def event(self, kind, message, **kwargs): self.events.append(message)
        def artifact(self, name, kind, content, *args): self.artifacts[name]=content
    ctx=RecordingContext(tmp_path,[result(0,"1 passed"),result(1,"1 failed"),result(0,"2 passed")])
    task=SimpleNamespace(base_sha="a"*40,task_id="tsk_test",limits=LIMITS,issue={"body":"Fix addition"},plan={})
    outcome=run_repair(task,ctx)
    assert outcome.failure_code==FailureCode.LLM_UNAVAILABLE and not outcome.final_diff
    assert ctx.attempts==([1] if fail_evaluation else [])
    assert ctx.budgets["modelCalls"]==1+len(calls)
    assert "gemini-4-argon" not in calls
    if fail_evaluation:
        assert "Evaluation incomplete" in ctx.artifacts["final_tests"]
        assert TEST+FIX==ctx.artifacts["attempt_001_diff"]


def test_repository_context_excludes_secrets_and_hidden_files(tmp_path):
    (tmp_path / ".env").write_text("SECRET_VALUE")
    (tmp_path / "credentials.txt").write_text("CREDENTIAL_VALUE")
    (tmp_path / "app.py").write_text("print('source')")
    text = repository_context(tmp_path)
    assert "source" in text
    assert "SECRET_VALUE" not in text and "CREDENTIAL_VALUE" not in text


def test_compact_edits_build_valid_identical_regression_sections():
    proposal = EditProposal(explanation="add correctly", tests=[{"path": "test_new.py", "content": "def test_add():\n    assert 1 + 2 == 3\n"}],
                            edits=[{"path": "calc.py", "old_text": "return a - b", "new_text": "return a + b"}])
    compiled = compile_edits(proposal, {"calc.py": "def add(a, b):\n    return a - b\n"})
    validate_proposal(compiled, LIMITS, "a" * 40)
    assert compiled.final_diff.startswith(compiled.reproduction_diff)
    assert "-    return a - b" in compiled.final_diff


def test_compact_edits_reject_ambiguous_original_text():
    proposal = EditProposal(explanation="unsafe", tests=[{"path": "test_new.py", "content": "pass\n"}],
                            edits=[{"path": "calc.py", "old_text": "foo", "new_text": "bar"}])
    with pytest.raises(ValueError, match="exactly once"):
        compile_edits(proposal, {"calc.py": "foo foo\n"})


def test_compact_edits_support_source_without_final_newline():
    proposal = EditProposal(explanation="add", tests=[{"path": "test_new.py", "content": "def test_add():\n    assert 1 + 2 == 3\n"}],
                            edits=[{"path": "calc.py", "old_text": "return a - b", "new_text": "return a + b"}])
    compiled = compile_edits(proposal, {"calc.py": "def add(a, b):\n    return a - b"})
    assert "\\ No newline at end of file" in compiled.final_diff
    validate_proposal(compiled, LIMITS, "a" * 40)


def test_compiled_patch_really_applies_with_new_files_and_missing_newline(tmp_path):
    source = "def add(a, b):\n    return a - b"
    (tmp_path / "calc.py").write_text(source, encoding="utf-8", newline="")
    proposal = EditProposal(explanation="add", tests=[{"path": "test_new.py", "content": "def test_add():\n    assert 1 + 2 == 3\n"}],
                            edits=[{"path": "calc.py", "old_text": "return a - b", "new_text": "return a + b"}])
    compiled = compile_edits(proposal, {"calc.py": source})
    patch = tmp_path / "change.patch"
    patch.write_text(compiled.final_diff, encoding="utf-8", newline="")
    subprocess.run(["git", "apply", "--check", str(patch)], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "apply", str(patch)], cwd=tmp_path, check=True, capture_output=True)
    assert (tmp_path / "test_new.py").is_file()
    assert "return a + b" in (tmp_path / "calc.py").read_text()
