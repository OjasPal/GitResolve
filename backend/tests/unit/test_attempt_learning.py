from types import SimpleNamespace
from agent.attempt_memory import AttemptMemory
from agent.config import RouterSettings
from agent.providers import BaseProvider,ModelResult
from agent.router import ModelRouter
from app.agent.repair_runner import RepairProposal,run_repair
from app.agent.repair_runner import EditProposal,compile_edits
import pytest
from tests.unit.test_repair_runner import TEST,FIX,LIMITS,Context,result,approved_review


def test_failure_memory_requires_an_alternative_strategy_and_rejects_identical_candidate():
    memory=AttemptMemory()
    proposal=RepairProposal(explanation="fix",reproduction_diff=TEST,final_diff=TEST+FIX)
    memory.record(proposal,"Patched test suite did not pass: edge case still fails")
    assert memory.repeated(ModelResult("google","flash",parsed=proposal))
    assert memory.repeated(proposal.model_copy(update={"explanation":"A newly worded explanation of the same code"}))
    assert "edge case" in memory.prompt() and "Change the implementation/test strategy" in memory.prompt()
    memory.record(proposal,"Patched test suite did not pass: another failure")
    assert memory.persistent()


def test_normal_repairs_cannot_disguise_deletion_as_an_empty_file():
    proposal=EditProposal(explanation="Remove a file",tests=[{"path":"test_regression.py","content":"def test_case(): assert True\n"}],edits=[{"path":"app.py","old_text":"value=42\n","new_text":""}])
    with pytest.raises(ValueError,match="Unsafe whole-file removal"):
        compile_edits(proposal,{"app.py":"value=42\n"})


def test_repeated_failed_implementation_requests_specialist_and_uses_new_patch(tmp_path,monkeypatch):
    (tmp_path/"math_example.py").write_text("def add(a, b):\n    return a - b\n")
    first=RepairProposal(explanation="First approach",reproduction_diff=TEST,final_diff=TEST+FIX)
    alternative=RepairProposal(explanation="Alternative implementation",reproduction_diff=TEST,final_diff=TEST+FIX.replace("a + b","sum((a, b))"))
    replies=iter([first,first,alternative,approved_review()]);calls=[];prompts=[]
    class Provider(BaseProvider):
        def generate(self,model_id,prompt,**kwargs):
            calls.append(model_id);prompts.append(prompt)
            return ModelResult("google",model_id,parsed=next(replies))
    router=ModelRouter(RouterSettings(),provider_factory=lambda _:Provider())
    monkeypatch.setattr("agent.router.get_router",lambda:router)
    class Recording(Context):
        def event(self,kind,message,**kwargs):self.events.append(message)
    ctx=Recording(tmp_path,[result(0,"1 passed"),result(1,"1 failed"),result(1,"1 failed: edge case still fails"),result(1,"1 failed"),result(0,"2 passed")])
    task=SimpleNamespace(base_sha="a"*40,task_id="tsk_learning",limits={**LIMITS,"maxRepairAttempts":6},issue={"body":"Correct addition"},plan={})
    outcome=run_repair(task,ctx)
    assert outcome.outcome=="fix_ready" and "sum((a, b))" in outcome.final_diff
    assert calls==[router.settings.primary.model_id,router.settings.primary.model_id,router.settings.specialist.model_id,router.settings.primary.model_id]
    assert ctx.budgets["modelCalls"]==5
    assert "edge case still fails" in prompts[1] and "Required strategy change" in prompts[1]
    assert "already rejected proposal" in prompts[2]
