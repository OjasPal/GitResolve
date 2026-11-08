from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
import pytest

from githubapp.cache import TTLCache
from githubapp.client import GitHubClient, GitHubClientError
from githubapp.auth import GitHubAuth
from sandbox.baseline_cache import key_for, remember, CACHE
from sandbox.models import ExecutionRequest, ExecutionResult


def test_ttl_bounds_and_copy_isolation():
    now = [0]
    cache = TTLCache(ttl=5, capacity=2, clock=lambda: now[0])
    cache.put('alice', {'items': [1]})
    cache.get('alice')['items'].append(2)
    assert cache.get('alice') == {'items': [1]}
    assert cache.get('bob') is None
    now[0] = 5
    assert cache.get('alice') is None
    for n in range(3): cache.put(n, n)
    assert cache.get(0) is None


def test_etag_304_and_revocation_never_fall_back_to_cached_body(monkeypatch):
    cache, seen = TTLCache(), []
    responses = iter([
        SimpleNamespace(status_code=200, headers={'ETag': 'one'}, json=lambda: [{'id': 1}]),
        SimpleNamespace(status_code=304, headers={}),
        SimpleNamespace(status_code=403, headers={}),
    ])
    def get(*args, **kwargs):
        seen.append(kwargs['headers'])
        return next(responses)
    monkeypatch.setattr('githubapp.client.requests.get', get)
    client = GitHubClient('token', on_rejected=cache.clear)
    assert client.request('GET', '/repos', etag_cache=cache, cache_key='alice') == [{'id': 1}]
    assert client.request('GET', '/repos', etag_cache=cache, cache_key='alice') == [{'id': 1}]
    assert seen[1]['If-None-Match'] == 'one'
    with pytest.raises(GitHubClientError, match='access_revoked'):
        client.request('GET', '/repos', etag_cache=cache, cache_key='alice')
    assert cache.get('alice') is None and cache.get('bob') is None


def test_tokens_are_scoped_expiring_and_force_refreshed(monkeypatch):
    auth = GitHubAuth('1', 'unused')
    calls = []
    monkeypatch.setattr(auth, '_generate_jwt', lambda: 'jwt')
    def request(self, method, path, **kwargs):
        calls.append((path, kwargs))
        return {'token': str(len(calls)), 'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
    monkeypatch.setattr(GitHubClient, 'request', request)
    kw = {'repositories': ['r'], 'permissions': {'contents': 'read'}}
    assert auth.get_installation_token(1, **kw) == auth.get_installation_token(1, **kw)
    assert len(calls) == 1
    assert auth.get_installation_token(2, **kw) == '2'
    assert auth.get_installation_token(1, repositories=['other']) == '3'
    assert auth.get_installation_token(1, **kw, force_refresh=True) == '4'
    auth.invalidate_tokens()
    assert auth.get_installation_token(1, **kw) == '5'


def test_rejected_token_is_replaced_once_then_fails_closed(monkeypatch):
    seen, invalidated = [], []
    def get(*args, **kwargs):
        seen.append(kwargs['headers']['Authorization'])
        return SimpleNamespace(status_code=401, headers={})
    monkeypatch.setattr('githubapp.client.requests.get', get)
    client = GitHubClient('old', refresh=lambda: 'new', on_rejected=lambda: invalidated.append(True))
    with pytest.raises(GitHubClientError, match='access_revoked'): client._get('/repos')
    assert seen == ['Bearer old', 'Bearer new']
    assert len(invalidated) == 2


def request(**updates):
    return ExecutionRequest(execution_id='test', repo_url='https://github.com/o/r', commit_sha='a'*40,
                            command=['python', '-m', 'pytest', '-q'], **updates)


def test_baseline_keys_include_every_verification_boundary():
    base = request()
    key = key_for(base, 'alice:installation', 'env', 'image')
    for changed in [base.model_copy(update={'commit_sha': 'b'*40}),
                    base.model_copy(update={'command': ['python', '-m', 'pytest', 'test_x.py']}),
                    base.model_copy(update={'require_ui_checks': True}),
                    base.model_copy(update={'repo_url': 'https://github.com/o/other'})]:
        assert key_for(changed, 'alice:installation', 'env', 'image') != key
    assert key_for(base, 'bob:installation', 'env', 'image') != key
    assert key_for(base, 'alice:installation', 'new-env', 'image') != key
    assert key_for(base, 'alice:installation', 'env', 'new-image') != key
    assert key_for(base.model_copy(update={'patch': 'any patch'}), 'alice', 'env', 'image') is None


@pytest.mark.parametrize('change', [{'status': 'failed'}, {'exit_code': 1}, {'cleanup_ok': False},
    {'stdout': 'no tests ran'}, {'stdout_truncated': True}, {'oom_killed': True}, {'timed_out': True}])
def test_only_successful_complete_baselines_are_cached(change):
    CACHE.clear()
    good = ExecutionResult(execution_id='test', status='passed', exit_code=0, stdout='1 passed')
    remember('good', good)
    assert CACHE.get('good') is not None
    remember('bad', good.model_copy(update=change))
    assert CACHE.get('bad') is None


def test_prewarm_is_bounded_and_stops_on_rejected_plan(monkeypatch):
    from app.worker import prewarm
    pending = []
    class Thread:
        def __init__(self, *, target, args, **kwargs): self.fn = lambda: target(*args)
        def start(self): pending.append(self.fn)
    monkeypatch.setattr(prewarm.threading, 'Thread', Thread)
    store = SimpleNamespace(get=lambda path: {'status': 'rejected', 'baseSha': 'a'*40})
    gateway = SimpleNamespace(source=lambda task: pytest.fail('rejected plans must not acquire code'))
    assert prewarm.start(SimpleNamespace(store=store), gateway, 'test')
    assert not prewarm.start(SimpleNamespace(store=store), gateway, 'second')
    pending.pop()()
    assert prewarm.start(SimpleNamespace(store=store), gateway, 'third')
    pending.pop()()


def test_listing_pages_are_user_scoped_and_cached_display_never_authorizes_writes(monkeypatch):
    from app.services.github_real import RealGitHubGateway
    from app.services.store.memory import MemoryStore
    from app.core.config import Settings
    store = MemoryStore()
    store.set('users/1', {'githubLogin': 'alice'})
    store.set('users/2', {'githubLogin': 'bob'})
    auth = SimpleNamespace(app_client=lambda: SimpleNamespace(pages=lambda path: [{'id': 10}]),
                           get_installation_token=lambda *a, **kw: 'token')
    gateway = RealGitHubGateway(Settings(_env_file=None), store, auth=auth)
    repo = {'id': 20, 'full_name': 'org/shared', 'default_branch': 'main', 'private': True}
    calls, revoked = [], [False]
    def request(self, method, path, **kwargs):
        calls.append(path)
        if path == '/installation/repositories': return {'repositories': [repo]}
        if path == '/repos/org/shared': return repo
        if '/collaborators/alice/' in path:
            return {'permission': 'read' if revoked[0] else 'write', 'user': {'id': 1}}
        if '/collaborators/bob/' in path: return {'permission': 'read', 'user': {'id': 2}}
        raise AssertionError(path)
    monkeypatch.setattr(GitHubClient, 'request', request)
    alice, next_page = gateway.repository_page('1')
    assert len(alice) == 1 and next_page is None
    count = len(calls)
    assert gateway.repository_page('1')[0] == alice and len(calls) == count
    assert gateway.repository_page('2') == ([], None)
    revoked[0] = True
    with pytest.raises(GitHubClientError, match='access_revoked'):
        gateway.authorized_client('1', '10_20', write=True)
    assert gateway.repository_page('1') == ([], None)


def test_page_continues_even_when_all_candidates_on_page_are_inaccessible(monkeypatch):
    from app.services.github_real import RealGitHubGateway
    from app.services.store.memory import MemoryStore
    from app.core.config import Settings
    store = MemoryStore(); store.set('users/1', {'githubLogin': 'alice'})
    auth = SimpleNamespace(app_client=lambda: SimpleNamespace(pages=lambda path: [{'id': 10}]),
                           get_installation_token=lambda *a, **kw: 'token')
    gateway = RealGitHubGateway(Settings(_env_file=None), store, auth=auth)
    def request(self, method, path, **kwargs):
        if path == '/installation/repositories':
            return {'repositories': [{'id': n, 'full_name': f'org/r{n}', 'default_branch': 'main', 'private': True}
                                    for n in range(30 if kwargs['params']['page'] == 1 else 1)]}
        return {'permission': 'read', 'user': {'id': 1}}
    monkeypatch.setattr(GitHubClient, 'request', request)
    assert gateway.repository_page('1', 1) == ([], 2)
    assert gateway.repository_page('1', 2) == ([], None)


def test_fresh_repository_lookup_preserves_gateway_error_contract(monkeypatch):
    from app.services.github_real import RealGitHubGateway
    from app.services.github import GitHubUnavailable
    from app.services.store.memory import MemoryStore
    from app.core.config import Settings
    gateway = RealGitHubGateway(Settings(_env_file=None), MemoryStore(), auth=SimpleNamespace())
    def unavailable(*args): raise GitHubClientError('github_rate_limited')
    monkeypatch.setattr(gateway, '_get_repository', unavailable)
    with pytest.raises(GitHubUnavailable, match='github_rate_limited'):
        gateway.get_repository('1', '10_20')
