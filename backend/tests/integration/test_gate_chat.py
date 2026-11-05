import pytest
from agent.config import RouterSettings
from agent.providers import BaseProvider, ModelResult, FatalProviderError
from agent.observability import create_usage_record
from agent.router import ModelRouter
from app.services.gate_chat import GateAnswer
from tests.conftest import API
from tests.integration.test_task_lifecycle import drive_to_awaiting_approval


@pytest.fixture
def model(monkeypatch):
    class Provider(BaseProvider):
        def __init__(self): self.calls = []; self.hook = None; self.fail = False
        def generate(self, model_id, prompt, **kwargs):
            self.calls.append(prompt)
            if self.hook: self.hook()
            if self.fail: raise FatalProviderError("unavailable", "google", model_id, status_code=403)
            return ModelResult("google", model_id, parsed=GateAnswer(answer="The recorded plan is ready for review.", revised_request="Preserve existing behavior and add quiz export."), usage=create_usage_record("google",model_id,10,5))
    provider = Provider()
    router = ModelRouter(RouterSettings(max_provider_retries=0), provider_factory=lambda _:provider)
    monkeypatch.setattr("agent.router.get_router", lambda:router)
    return provider


def send(env,h,tid,operation="ask",message="Explain this approach",revision=0,request_id="request-unique-0001"):
    return env.client.post(f"{API}/tasks/{tid}/conversation", headers=h, json={"message":message,"operation":operation,"revision":revision,"requestId":request_id})


def plan_gate(env,h):
    tid=env.create(h).json()["id"]
    env.run_worker()
    return tid


def test_questions_are_grounded_idempotent_and_cannot_change_plan(env,model):
    h=env.headers();tid=plan_gate(env,h);before=env.get(h,tid)
    result=send(env,h,tid)
    assert result.status_code==200,result.text
    task=result.json()
    assert task["plan"]==before["plan"] and task["status"]=="awaiting_start"
    assert task["budgets"]["modelCalls"]==before["budgets"]["modelCalls"]+1
    assert len(task["gateMessages"])==2 and not task["gateChatBusy"]
    assert '"plan"' in model.calls[0]
    assert send(env,h,tid).status_code==200 and len(model.calls)==1
    assert send(env,env.headers("2002"),tid).status_code==404


def test_plan_changes_queue_new_investigation_and_new_approval(env,model):
    h=env.headers();tid=plan_gate(env,h)
    result=send(env,h,tid,"revise","Add export without changing generation")
    assert result.status_code==200,result.text
    assert result.json()["status"]=="queued" and result.json()["plan"] is None
    assert result.json()["issue"]["body"]=="Preserve existing behavior and add quiz export."
    assert result.json()["gateRevision"]==1
    assert env.run_worker()
    assert env.get(h,tid)["status"]=="awaiting_start"
    assert send(env,h,tid,revision=0,request_id="another-request-002").status_code==409


def test_prompt_edit_is_exact_and_replans_without_unnecessary_chat_call(env,model):
    h=env.headers();tid=plan_gate(env,h)
    result=send(env,h,tid,"prompt","My new request, exactly.")
    assert result.status_code==200,result.text
    assert result.json()["issue"]["body"]=="My new request, exactly."
    assert result.json()["status"]=="queued" and not model.calls


def test_diff_questions_preserve_hash_but_changes_invalidate_it(env,model):
    h=env.headers();task=drive_to_awaiting_approval(env,h);tid=task["id"]
    result=send(env,h,tid)
    assert result.json()["finalDiffHash"]==task["finalDiffHash"]
    assert send(env,h,tid,"prompt","Different goal",request_id="edit-original-0002").status_code==409
    result=send(env,h,tid,"revise","Also preserve the download filename",request_id="revise-review-0003")
    assert result.status_code==200,result.text
    changed=result.json()
    assert changed["status"]=="running" and changed["finalDiffHash"] is None
    assert changed["approval"] is None and changed["attempts"]==task["attempts"]
    assert changed["issue"]["body"]==task["issue"]["body"]
    assert env.post(h,tid,"approve",{"diffHash":task["finalDiffHash"]}).status_code==409
    stored=env.store.get(f"tasks/{tid}")
    assert "download filename" in stored["revisionContext"] and "sandboxVerification" not in stored


def test_chat_blocks_approval_while_reply_is_in_flight(env,model):
    h=env.headers();tid=plan_gate(env,h)
    model.hook=lambda: (_ for _ in ()).throw(AssertionError()) if env.post(h,tid,"start").status_code!=409 else None
    assert send(env,h,tid).status_code==200
    assert env.post(h,tid,"start").status_code==200
    assert send(env,h,tid,request_id="outside-gate-00002").status_code==409


def test_provider_failure_unlocks_gate_without_changing_review(env,model):
    h=env.headers();tid=plan_gate(env,h);before=env.get(h,tid)
    model.fail=True
    result=send(env,h,tid)
    assert result.status_code==502
    after=env.get(h,tid)
    assert not after["gateChatBusy"] and after["plan"]==before["plan"]
    assert after["budgets"]["modelCalls"]==before["budgets"]["modelCalls"]+1


def test_changed_review_rejects_previous_approval_even_for_identical_patch(env,model):
    h=env.headers();task=drive_to_awaiting_approval(env,h);tid=task["id"]
    result=send(env,h,tid,"revise","Preserve the export filename")
    assert result.status_code==200,result.text
    env.run_worker()
    reviewed=env.get(h,tid)
    assert reviewed["status"]=="awaiting_approval"
    assert reviewed["gateRevision"]==1
    assert env.post(h,tid,"approve",{"diffHash":reviewed["finalDiffHash"],"revision":0}).status_code==409
    assert env.post(h,tid,"approve",{"diffHash":reviewed["finalDiffHash"],"revision":1}).status_code==200


def test_model_initialization_failure_releases_conversation_lock(env,model,monkeypatch):
    h=env.headers();tid=plan_gate(env,h)
    def fail(): raise ValueError("provider configuration unavailable")
    monkeypatch.setattr("agent.router.get_router",fail)
    assert send(env,h,tid).status_code==502
    stored=env.store.get(f"tasks/{tid}")
    assert not stored.get("gateChatBusy")
    assert env.post(h,tid,"start").status_code==200


def test_repair_limit_is_checked_before_requesting_a_model_reply(env,model):
    h=env.headers();task=drive_to_awaiting_approval(env,h);tid=task["id"]
    env.store.run_transaction(lambda tx: tx.update(f"tasks/{tid}",{"attempts":{"used":task["limits"]["maxRepairAttempts"]}}))
    assert send(env,h,tid,"revise","Change the layout").status_code==429
    assert not model.calls
