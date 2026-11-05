from agent.config import RouterSettings
from agent.providers import BaseProvider, RetryableProviderError
from agent.router import ModelRouter
from app.agent.fake import FakeAgentRunner
from app.agent.interface import ExecuteResult
from app.agent.langgraph_adapter import LangGraphAgentRunner
from app.schemas.enums import ArtifactType, FailureCode, TaskStage


def test_planning_outage_is_retried_with_a_bound(env,monkeypatch):
    calls=[]
    class Provider(BaseProvider):
        def generate(self,model_id,**kwargs):
            calls.append(model_id)
            raise RetryableProviderError("deadline exceeded","google",model_id,504)
    router=ModelRouter(RouterSettings(),provider_factory=lambda _:Provider())
    monkeypatch.setattr("agent.router._global_router",router)
    env.worker.agent=LangGraphAgentRunner()
    h=env.headers();tid=env.create(h).json()["id"]
    env.run_worker()
    task=env.get(h,tid)
    assert task["status"] == "queued" and task["outcome"]["kind"] == "infra_retry"
    assert task["attempts"]["used"]==0 and task["budgets"]["modelCalls"]==2
    for _ in range(2):
        assert env.run_worker()
    task = env.get(h, tid)
    assert task["status"] == "failed_infra" and task["failure"]["code"] == "llm_unavailable"
    assert task["outcome"]["kind"] == "cannot_fix_with_report"
    assert not task["finalDiffHash"] and not task["pr"]
    assert not env.run_worker() and 2 <= len(calls) <= 6
    assert env.post(h,tid,"approve",{"diffHash":"sha256:"+"a"*64}).status_code==409


def test_incomplete_evaluation_never_offers_approval_or_publishes(env):
    class PausedAgent(FakeAgentRunner):
        def execute(self,task,ctx):
            ctx.stage(TaskStage.EVALUATE)
            ctx.artifact("final_tests",ArtifactType.FINAL_TESTS,"Tests finished. Mandatory evaluation incomplete.")
            return ExecuteResult("failed",failure_code=FailureCode.LLM_UNAVAILABLE,
                failure_message="Evaluation incomplete. Evidence is saved and publication is blocked.")
    env.worker.agent=PausedAgent()
    h=env.headers();tid=env.create(h).json()["id"]
    env.run_worker();env.post(h,tid,"start");env.run_worker()
    task=env.get(h,tid)
    assert task["status"]=="failed_infra" and task["stage"]=="evaluate"
    assert not task["finalDiffHash"] and not task["pr"]
    assert "approve" not in task["availableActions"]
    assert env.store.get(f"tasks/{tid}/artifacts/final_tests")["content"].endswith("incomplete.")
    assert not env.run_worker()
