"""Real gateway adapter and publication guard tests with mocked GitHub I/O."""
from types import SimpleNamespace
import base64
import hashlib
import pytest

from app.core.config import Settings
from app.services.github_real import RealGitHubGateway
from app.services.store.memory import MemoryStore
from app.worker.github_pr import GitHubPullRequestCreator
from app.worker.pr import PullRequestError
from tests.integration.test_task_lifecycle import drive_to_awaiting_approval

from githubapp.client import GitHubClientError
from githubapp.identity import BotIdentity

IDENTITY = BotIdentity('gitresolve-agent[bot]', 351129099)


class MockAPI:
    def __init__(self):
        self.calls = []
        self.existing = []
    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path.endswith('/pulls'):
            return self.existing if method == 'GET' else {
                'number': 7, 'html_url': 'https://github.com/demo/seeded-bugs/pull/7'}
        if '/git/commits/' in path:
            return {'tree': {'sha': 'b'*40}}
        if '/git/trees/' in path:
            return {'tree': [{'path': 'src/slugify.py', 'type': 'blob', 'mode': '100644', 'sha': 'c'*40}]}
        if '/git/blobs/' in path:
            return {'content': base64.b64encode(b'def slugify(text):\n    return re.sub(r"[^a-z0-9]+", "-", text.lower())\n').decode()}
        if path.endswith(('/git/trees', '/git/commits')):
            return {'sha': 'd'*40}
        if path.endswith('/git/refs'):
            return {'ref': kwargs['json']['ref']}
        raise AssertionError((method, path))
    def _get(self, path):
        return self.request('GET', path)


def approved(env):
    headers = env.headers()
    task = drive_to_awaiting_approval(env, headers)
    env.post(headers, task['id'], 'approve', {'diffHash': task['finalDiffHash']})
    task = env.store.get('tasks/' + task['id'])
    task['sandboxVerification'] = {'source': 'docker', 'passed': True, 'testsRun': 13,
        'command': ['python', '-m', 'pytest'], 'baseSha': task['baseSha'], 'diffHash': task['finalDiffHash']}
    diff = env.store.get(f"tasks/{task['id']}/artifacts/final_diff")['content']
    api = MockAPI()
    gateway = SimpleNamespace(store=env.store, verified_bot_identity=lambda client: IDENTITY,
                              authorized_client=lambda *a, **kw:
                              (SimpleNamespace(full_name=task['repoFullName']), api))
    return task, diff, api, GitHubPullRequestCreator(gateway)


@pytest.mark.parametrize('mutation', ['missing_receipt', 'failed_tests', 'wrong_diff', 'unapproved', 'no_tests', 'wrong_commit'])
def test_publication_fails_closed_before_any_api_call(env, mutation):
    task, diff, api, creator = approved(env)
    if mutation == 'missing_receipt': task.pop('sandboxVerification')
    if mutation == 'failed_tests': task['sandboxVerification']['passed'] = False
    if mutation == 'wrong_diff': diff += '\n'
    if mutation == 'unapproved': task['approval']['state'] = 'pending'
    if mutation == 'no_tests': task['sandboxVerification']['testsRun'] = 0
    if mutation == 'wrong_commit': task['sandboxVerification']['baseSha'] = 'f'*40
    with pytest.raises(PullRequestError): creator.create(task, diff)
    assert api.calls == []


def test_publication_writes_only_approved_tree_then_draft_pr(env):
    task, diff, api, creator = approved(env)
    checkpoints, events = [], []
    result = creator.create(task, diff, checkpoint=lambda: checkpoints.append(True), event=events.append)
    assert result.url.endswith('/pull/7') and len(checkpoints) == 5
    writes = [(p, kw['json']) for method, p, kw in api.calls if method == 'POST']
    assert [p.split('/')[-1] for p, _ in writes] == ['trees', 'commits', 'refs', 'pulls']
    assert writes[0][1]['tree'][0]['content'].endswith('.strip("-")\n')
    assert writes[-1][1]['draft'] is True and writes[-1][1]['base'] == 'main'
    assert 'tsk_' not in writes[1][1]['message'] and len(writes[1][1]['message']) <= 72
    assert result.branch.startswith('gitresolve-agent/') and 'tsk_' not in result.branch
    assert writes[-1][1]['title'] == writes[1][1]['message']
    commit = writes[1][1]
    expected = '351129099+gitresolve-agent[bot]@users.noreply.github.com'
    for role in ('author', 'committer'):
        assert commit[role]['name'] == 'GitResolve Agent'
        assert commit[role]['email'] == expected
    assert commit['author'] == commit['committer']
    assert 'gitresolve@users.noreply.github.com' not in str(commit)
    assert events and not any('/merge' in path for _, path, _ in api.calls)


def test_publication_retry_reuses_identified_pr_without_writing(env):
    task, diff, api, creator = approved(env)
    from githubapp.patch import apply_diff
    files = apply_diff(diff, lambda name: ('def slugify(text):\n    return re.sub(r"[^a-z0-9]+", "-", text.lower())\n', '100644'))
    tree = []
    for item in files:
        content = item['content'].encode()
        tree.append({'path': item['path'], 'mode': item['mode'], 'type': 'blob',
                     'sha': hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest()})
    original = api.request
    def request(method, path, **kw):
        if path.endswith('/git/commits/head'):
            return {'tree': {'sha': 'result'}, 'parents': [{'sha': task['baseSha']}]}
        if path.endswith('/git/trees/result'):
            return {'tree': tree}
        return original(method, path, **kw)
    api.request = request
    api.existing = [{'number': 7, 'html_url': 'https://github.com/demo/seeded-bugs/pull/7',
        'base': {'ref': 'main'}, 'head': {'sha': 'head'},
        'body': f"<!-- gitresolve:{task['id']}:{task['finalDiffHash']} -->"}]
    assert creator.create(task, diff).number == 7
    assert all(method == 'GET' for method, _, _ in api.calls)
    tree[0]['sha'] = 'tampered'
    with pytest.raises(PullRequestError):
        creator.create(task, diff)


def test_publication_uses_reviewed_summary_instead_of_the_user_prompt(env):
    task, diff, api, creator = approved(env)
    task["issue"]["title"] = "Improve the UI. Do not add React or Next.js."
    task["finalDiffSummary"]["commitMessage"] = "fix: Restore readable quiz cards"
    creator.create(task, diff)
    commit = next(kw["json"] for method, path, kw in api.calls if method == "POST" and path.endswith("/git/commits"))
    assert commit["message"] == "fix: Restore readable quiz cards"


def test_cleanup_tree_deletes_exact_files_and_reconciles_a_lost_response(env,tmp_path):
    from sandbox.cleanup import build_scope,deletion_patch
    from app.services.ids import sha256_hash
    task,_,api,creator=approved(env)
    request="Remove all four root test .py files safely"
    for i in range(4):(tmp_path/f"test_old_{i}.py").write_bytes(b"def test_old(): assert True\n")
    scope={**build_scope(tmp_path,request,task["baseSha"]),"approved":True}
    patch=deletion_patch(tmp_path,scope);digest=sha256_hash(patch)
    task.update(cleanupScope=scope,issue={**task["issue"],"body":request},finalDiffHash=digest,
        finalDiffSummary={"commitMessage":"chore: Remove unused root test files","branchName":"remove-root-tests"})
    task["approval"]["diffHash"]=digest
    task["sandboxVerification"].update(diffHash=digest,cleanupVerified=True,allowedDeletions=[f["path"] for f in scope["files"]])
    source={'path':'src/slugify.py','mode':'100644','type':'blob','sha':'c'*40}
    base=[source]+[{'path':f['path'],'mode':'100644','type':'blob','sha':'e'*40} for f in scope['files']]
    original=api.request
    def request_api(method,path,**kwargs):
        if path.endswith('/git/trees/'+'b'*40):return {'tree':base}
        if path.endswith('/git/blobs/'+'e'*40):return {'content':base64.b64encode(b"def test_old(): assert True\n").decode()}
        if path.endswith('/git/commits/head'):return {'tree':{'sha':'result'},'parents':[{'sha':task['baseSha']}]}
        if path.endswith('/git/trees/result'):return {'tree':[source]}
        return original(method,path,**kwargs)
    api.request=request_api
    assert creator.create(task,patch).number==7
    tree=next(kw['json']['tree'] for method,path,kw in api.calls if method=='POST' and path.endswith('/git/trees'))
    assert len(tree)==4 and all(entry['sha'] is None and 'content' not in entry for entry in tree)
    api.calls.clear()
    api.existing=[{'number':7,'html_url':'https://github.com/demo/seeded-bugs/pull/7','base':{'ref':'main'},'head':{'sha':'head'},'body':f"<!-- gitresolve:{task['id']}:{digest} -->"}]
    assert creator.create(task,patch).number==7
    assert all(method=='GET' for method,_,_ in api.calls)


def test_gateway_checks_fresh_user_permission_and_scopes_tokens(monkeypatch):
    store = MemoryStore()
    store.set('users/1', {'githubLogin': 'alice', 'installationIds': [10]})
    scopes = []
    auth = SimpleNamespace(get_installation_token=lambda installation, **kw: scopes.append((installation, kw)) or 'secret')
    permissions = {'permission': 'read', 'user': {'id': 1}}
    monkeypatch.setattr('app.services.github_real.GitHubClient.pages', lambda *a, **kw:
        [{'id': 20, 'full_name': 'o/r', 'default_branch': 'main', 'private': True}])
    monkeypatch.setattr('app.services.github_real.GitHubClient._get', lambda *a: permissions)
    gateway = RealGitHubGateway(Settings(_env_file=None), store, auth=auth)
    repo, _ = gateway.authorized_client('1', '10_20')
    assert repo.private and scopes[-1][1]['repositories'] == ['r']
    assert scopes[-1][1]['permissions']['contents'] == 'read'
    permissions['permission'] = 'none'
    assert gateway.get_repository('1', '10_20') is None


def test_real_mode_refuses_public_development_sign_in(env):
    env.settings.github_mode = 'real'
    response = env.client.post('/api/v1/auth/dev-token', json={'userId': '1', 'githubLogin': 'owner'})
    assert response.status_code == 404

def test_real_execution_cannot_offer_unrun_tests_for_approval(env):
    headers = env.headers()
    tid = env.create(headers).json()['id']
    env.run_worker()
    env.post(headers, tid, 'start')
    env.settings.github_mode = 'real'
    env.run_worker()
    task = env.get(headers, tid)
    assert task['status'] == 'failed_policy' and task['pr'] is None


def test_cancel_during_publication_stops_before_branch_creation(env):
    from app.services.worker_service import CancelRequested
    task, diff, api, creator = approved(env)
    checks = []
    def checkpoint():
        checks.append(True)
        if len(checks) == 3:
            raise CancelRequested(task['id'])
    with pytest.raises(CancelRequested):
        creator.create(task, diff, checkpoint=checkpoint)
    assert [path.split('/')[-1] for method, path, _ in api.calls if method == 'POST'] == ['trees']


def test_real_issue_mapping_preserves_body_labels_comments_and_filters_prs(monkeypatch):
    store = MemoryStore()
    gateway = RealGitHubGateway(Settings(_env_file=None), store, auth=SimpleNamespace())
    data = {'number': 42, 'title': 'Real issue', 'body': 'Actual bug details',
            'html_url': 'https://github.com/o/r/issues/42', 'state': 'open', 'labels': [{'name': 'bug'}]}
    client = SimpleNamespace(_get=lambda path: data,
        request=lambda *a, **kw: [{'user': {'login': 'alice'}, 'body': 'Reproduction'}])
    monkeypatch.setattr(gateway, 'authorized_client', lambda *a: (SimpleNamespace(full_name='o/r'), client))
    issue = gateway.get_issue('1', '10_20', 42)
    assert issue.body == 'Actual bug details' and issue.labels == ['bug']
    assert issue.comments == [{'author': 'alice', 'body': 'Reproduction'}]
    data['pull_request'] = {}
    assert gateway.get_issue('1', '10_20', 42) is None


def test_oauth_returns_identity_without_persisting_github_token(monkeypatch):
    from app.services.github_real import AppOAuthProvider
    settings = Settings(github_app_client_id='client', github_app_client_secret='secret', _env_file=None)
    monkeypatch.setattr('app.services.github_real.requests.post', lambda *a, **kw:
        SimpleNamespace(status_code=200, json=lambda: {'access_token': 'github-user-secret'}))
    monkeypatch.setattr('app.services.github_real.GitHubClient._get', lambda *a:
        {'id': 123, 'login': 'alice', 'avatar_url': None})
    monkeypatch.setattr('app.services.github_real.GitHubClient.pages', lambda *a: [{'id': 10}])
    identity = AppOAuthProvider(settings).authenticate('code', 'http://localhost/callback')
    assert identity.user_id == '123' and identity.installation_ids == [10]
    assert 'secret' not in repr(identity)

@pytest.mark.parametrize('code', ['github_rate_limited', 'access_revoked', 'github_error'])
def test_github_worker_errors_become_safe_task_failures(env, code):
    from githubapp.client import GitHubClientError
    def resolve(task): raise GitHubClientError(code)
    env.worker.github = SimpleNamespace(resolve=resolve)
    headers = env.headers()
    tid = env.create(headers).json()['id']
    env.run_worker()
    task = env.get(headers, tid)
    assert task['status'] == 'failed_infra'
    assert task['failure']['code'] == code and task['pr'] is None

def test_publication_refuses_when_bot_identity_unverified(env):
    task, diff, api, creator = approved(env)
    def fail(client):
        raise GitHubClientError('bot_identity_invalid')
    creator.gateway.verified_bot_identity = fail
    with pytest.raises(PullRequestError):
        creator.create(task, diff)
    assert api.calls == []  # nothing read or written, no fallback identity


def test_gateway_verifies_bot_identity_once_via_app_jwt_client():
    app_client = SimpleNamespace(request=lambda m, p, **kw: {'id': 12345, 'slug': 'gitresolve-agent'})
    user_client = SimpleNamespace(request=lambda m, p, **kw:
        {'login': 'gitresolve-agent[bot]', 'id': 351129099, 'type': 'Bot'})
    calls = []
    auth = SimpleNamespace(app_id='12345', app_client=lambda: calls.append(1) or app_client)
    gateway = RealGitHubGateway(Settings(_env_file=None), MemoryStore(), auth=auth)
    first = gateway.verified_bot_identity(user_client)
    assert first.email == '351129099+gitresolve-agent[bot]@users.noreply.github.com'
    assert gateway.verified_bot_identity(user_client) is first and calls == [1]


def test_gateway_rejects_wrong_app_id():
    app_client = SimpleNamespace(request=lambda m, p, **kw: {'id': 999, 'slug': 'gitresolve-agent'})
    auth = SimpleNamespace(app_id='12345', app_client=lambda: app_client)
    gateway = RealGitHubGateway(Settings(_env_file=None), MemoryStore(), auth=auth)
    with pytest.raises(GitHubClientError):
        gateway.verified_bot_identity(SimpleNamespace())
