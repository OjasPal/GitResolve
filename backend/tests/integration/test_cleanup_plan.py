from contextlib import contextmanager
from app.agent.langgraph_adapter import LangGraphAgentRunner
from app.worker.context import JobContext
from tests.conftest import API,REPO_ID

REQUEST="There are four test .py files in the root directory prefixed with test. Remove all 4 .py files safely"


def test_cleanup_scope_requires_gate_one_and_never_changes_normal_limits(env,tmp_path,monkeypatch):
    for i in range(4):(tmp_path/f"test_old_{i}.py").write_text("# source\n"*40)
    (tmp_path/"app.py").write_text("value=42\n")
    @contextmanager
    def repository(self):yield tmp_path
    monkeypatch.setattr(JobContext,"repository",repository)
    def unexpected():raise AssertionError("Deterministic cleanup must not call a model")
    monkeypatch.setattr("agent.router.get_router",unexpected)
    env.worker.agent=LangGraphAgentRunner()
    h=env.headers()
    task=env.client.post(f"{API}/tasks",headers=h,json={"repoId":REPO_ID,"prompt":REQUEST}).json()
    tid=task["id"]
    env.store.run_transaction(lambda tx:tx.update("tasks/"+tid,{"baseSha":"a"*40}))
    env.run_worker()
    planned=env.get(h,tid)
    stored=env.store.get("tasks/"+tid)
    assert planned["status"]=="awaiting_start" and not stored["cleanupScope"]["approved"]
    assert len(planned["plan"]["likelyFiles"])==4 and planned["budgets"]["modelCalls"]==0
    assert "160" in " ".join(planned["plan"]["steps"])
    assert planned["limits"]["maxDiffLines"]==150
    env.store.run_transaction(lambda tx:tx.update("tasks/"+tid,{"gateRevision":1}))
    assert env.post(h,tid,"start").status_code==409
    assert env.post(h,tid,"start",{"revision":1}).status_code==200
    stored=env.store.get("tasks/"+tid)
    assert stored["cleanupScope"]["approved"] and stored["limits"]["maxDiffLines"]==160
    env.post(h,tid,"cancel")
    another=env.create(h).json()
    assert another["limits"]["maxDiffLines"]==150
