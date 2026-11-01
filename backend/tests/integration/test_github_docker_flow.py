"""API -> worker -> actual Docker -> human approval -> mocked GitHub -> API.

Repair is a deterministic test driver, not a replacement for the unfinished LLM
repair graph. The repository gateway is a local fixture; GitHub writes are mocked.
"""
from types import SimpleNamespace
import base64
import subprocess
import pytest

from app.agent.fake import FakeAgentRunner
from app.agent.interface import ExecuteResult
from app.schemas.enums import TaskStage
from app.services.github import FakeGitHubGateway
from app.worker.github_pr import GitHubPullRequestCreator
from sandbox.acquisition import acquire
from sandbox.test_runner import needs_docker
from tests.unit.test_github_real import MockAPI

DIFF = '''--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
--- /dev/null
+++ b/test_regression.py
@@ -0,0 +1,3 @@
+from calc import add
+def test_add():
+    assert add(2, 3) == 5
'''


@needs_docker
def test_fixture_api_docker_approval_publication(env, tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    original = 'def add(a, b):\n    return a - b\n'
    (repo / 'calc.py').write_text(original)
    (repo / 'test_before.py').write_text('from calc import add\ndef test_add():\n    assert add(2, 3) == 5\n')
    for args in (['init'], ['add', '.'], ['-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'fixture']):
        subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True)
    sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
    class API(MockAPI):
        def request(self, method, path, **kwargs):
            if '/git/trees/' in path:
                return {'tree': [{'path': 'calc.py', 'type': 'blob', 'mode': '100644', 'sha': 'c'*40}]}
            if '/git/blobs/' in path:
                return {'content': base64.b64encode(original.encode()).decode()}
            return super().request(method, path, **kwargs)
    api = API()
    class Gateway(FakeGitHubGateway):
        def resolve(self, task): return sha
        def source(self, task):
            def local(request, workspace, deadline, cancel):
                request = SimpleNamespace(repo_url=str(repo), commit_sha=sha)
                return acquire(request, workspace, deadline, cancel, allow_local_repo=True)
            return local
        def authorized_client(self, *args, **kwargs): return self.REPO, api
    class TestRepair(FakeAgentRunner):
        def execute(self, task, ctx):
            assert task.base_sha == sha
            before = ctx.execute(commit_sha=sha, command=['python', '-m', 'pytest', '-q'])
            assert before.status == 'failed' and before.exit_code == 1
            ctx.stage(TaskStage.TEST)
            after = ctx.execute(commit_sha=sha, command=['python', '-m', 'pytest', '-q'], patch=DIFF)
            assert after.status == 'passed' and after.exit_code == 0
            return ExecuteResult('fix_ready', DIFF, before.stdout + '\n' + after.stdout,
                {'filesChanged': 2, 'linesAdded': 4, 'linesRemoved': 1, 'files': ['calc.py', 'test_regression.py']},
                verification={'reproduced': True, 'regressionPassed': True, 'testsRun': 2, 'newFailures': []})
    env.worker.github = Gateway()
    env.worker.agent = TestRepair()
    env.worker.pr_creator = GitHubPullRequestCreator(env.worker.github)
    headers = env.headers()
    tid = env.create(headers).json()['id']
    assert env.run_worker()
    assert env.post(headers, tid, 'start').status_code == 200
    assert env.run_worker()
    task = env.get(headers, tid)
    assert task['status'] == 'awaiting_approval', task
    assert api.calls == []
    assert env.post(headers, tid, 'approve', {'diffHash': task['finalDiffHash']}).status_code == 200
    assert env.run_worker()
    task = env.get(headers, tid)
    assert task['status'] == 'completed', task
    assert task['pr']['url'].endswith('/pull/7') and task['pr']['branch'].startswith('gitresolve/')
    artifact = env.client.get(f'/api/v1/tasks/{tid}/artifacts/github_pr', headers=headers).json()
    assert artifact['type'] == 'pr_result' and task['pr']['url'] in artifact['content']


def test_real_graph_receives_pinned_checkout_and_releases_it(env, monkeypatch):
    from types import SimpleNamespace
    from app.agent.langgraph_adapter import LangGraphAgentRunner
    import agent.graph as graph_module
    from agent.analysis_schema import IssueAnalysis
    seen = []
    def source(task):
        def acquire(request, workspace, deadline, cancel):
            assert request.commit_sha == 'a'*40
            directory = workspace/'repo'
            directory.mkdir()
            (directory/'calc.py').write_text('def add(a, b): return a - b\n')
            seen.append(directory)
            return directory
        return acquire
    env.worker.github = SimpleNamespace(resolve=lambda task: 'a'*40, source=source)
    env.worker.agent = LangGraphAgentRunner()
    original = env.worker.agent._graph.invoke
    def invoke(state):
        from pathlib import Path
        assert state['base_sha'] == 'a'*40
        assert (Path(state['repo_path'])/'calc.py').is_file()
        return original(state)
    monkeypatch.setattr(env.worker.agent._graph, 'invoke', invoke)
    monkeypatch.setattr(graph_module, 'analyze_issue_with_gemini', lambda **kw: IssueAnalysis(
        problem_summary=kw['issue_body'], investigation_steps=['Inspect calc.py.'],
        needs_more_information=False, questions_for_reporter=[]))
    headers = env.headers()
    tid = env.create(headers).json()['id']
    env.run_worker()
    assert env.get(headers, tid)['status'] == 'awaiting_start'
    assert seen and not seen[0].exists()
