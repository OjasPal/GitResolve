"""Mocked API tests: no credentials or remote writes."""
from types import SimpleNamespace
import pytest
import requests
from githubapp.client import GitHubClient, GitHubClientError
from githubapp.patch import apply_diff


def test_rate_limit_is_safe_and_not_retried(monkeypatch):
    calls = []
    def get(*a, **k):
        calls.append(k)
        return SimpleNamespace(status_code=403, headers={'X-RateLimit-Remaining': '0'})
    monkeypatch.setattr(requests, 'get', get)
    with pytest.raises(GitHubClientError, match='github_rate_limited'):
        GitHubClient('secret')._get('/user')
    assert len(calls) == 1 and calls[0]['allow_redirects'] is False


def test_network_error_does_not_leak_credentials_or_retry_writes(monkeypatch):
    calls = []
    def post(*a, **k):
        calls.append(k)
        raise requests.ConnectionError('Bearer SECRET_PRIVATE_TOKEN')
    monkeypatch.setattr(requests, 'post', post)
    with pytest.raises(GitHubClientError) as error:
        GitHubClient('secret').request('POST', '/repos/a/b/pulls', json={})
    assert str(error.value) == 'github_error' and len(calls) == 1


def test_get_retries_only_transient_errors(monkeypatch):
    replies = iter([SimpleNamespace(status_code=503, headers={}),
                    SimpleNamespace(status_code=200, headers={}, json=lambda: {'ok': True})])
    monkeypatch.setattr(requests, 'get', lambda *a, **k: next(replies))
    monkeypatch.setattr('githubapp.client.time.sleep', lambda n: None)
    assert GitHubClient('secret')._get('/user') == {'ok': True}


def test_branch_is_url_encoded(monkeypatch):
    calls = []
    monkeypatch.setattr(requests, 'get', lambda url, **kw: calls.append(url) or
                        SimpleNamespace(status_code=200, json=lambda: {'commit': {'sha': 'a'*40}}))
    GitHubClient('secret').resolve_branch_sha('o', 'r', 'feature/one')
    assert calls[0].endswith('/branches/feature%2Fone')


def test_patch_exact_context_and_new_file():
    diff = '--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n--- /dev/null\n+++ b/test_a.py\n@@ -0,0 +1 @@\n+assert True\n'
    files = apply_diff(diff, lambda name: ('old\n', '100755'))
    assert files[0]['content'] == 'new\n' and files[0]['mode'] == '100755'
    assert files[1]['content'] == 'assert True\n'
    with pytest.raises(GitHubClientError, match='patch_conflict'):
        apply_diff(diff, lambda name: ('different\n', '100644'))


@pytest.mark.parametrize('diff', [
    '--- a/x\n+++ b/x\n@@ -1,2 +1 @@\n-x\n+y\n',
    '--- a/x\n+++ b/../../x\n@@ -1 +1 @@\n-x\n+y\n',
    '--- a/x\n+++ b/x\n@@ -99 +1 @@\n-x\n+y\n',
])
def test_patch_rejects_malformed_hunks(diff):
    with pytest.raises(GitHubClientError):
        apply_diff(diff, lambda name: ('x\n', '100644'))

def test_rejected_token_is_refreshed_once(monkeypatch):
    calls, refresh = [], []
    def get(*a, **kw):
        calls.append(kw['headers']['Authorization'])
        if len(calls) == 1:
            return SimpleNamespace(status_code=401, headers={})
        return SimpleNamespace(status_code=200, headers={}, json=lambda: {'ok': True})
    monkeypatch.setattr(requests, 'get', get)
    client = GitHubClient('old', refresh=lambda: refresh.append(True) or 'fresh')
    assert client._get('/user')['ok']
    assert calls == ['Bearer old', 'Bearer fresh'] and refresh == [True]
