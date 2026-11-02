"""Private archive and auth tests ensure credentials stay in the host gateway."""
import io
import tarfile
import time
from types import SimpleNamespace

import jwt
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption

from githubapp.auth import GitHubAuth
from githubapp.checkout import acquire_archive
from githubapp.client import GitHubClient, GitHubClientError


def test_signed_app_jwt_and_narrow_installation_token(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    calls = []
    def request(self, method, path, **kw):
        token = self._headers['Authorization'].split(' ')[1]
        claims = jwt.decode(token, key.public_key(), algorithms=['RS256'])
        assert claims['iss'] == '123' and 0 < claims['exp'] - time.time() < 600
        calls.append((method, path, kw))
        return {'token': 'temporary-installation-token'}
    monkeypatch.setattr(GitHubClient, 'request', request)
    assert GitHubAuth('123', pem).get_installation_token(10, ['r'], {'contents': 'read'}) == 'temporary-installation-token'
    assert calls[0][2]['json'] == {'repositories': ['r'], 'permissions': {'contents': 'read'}}


class Response:
    def __init__(self, status, headers=None, content=b''):
        self.status_code, self.headers, self.content = status, headers or {}, content
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def iter_content(self, size): yield self.content


def tar_bytes():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        entry = tarfile.TarInfo('owner-repo-sha/test_ok.py')
        content = b'def test_ok():\n    assert True\n'
        entry.size = len(content); entry.mode = 0o644
        archive.addfile(entry, io.BytesIO(content))
    return buffer.getvalue()


def test_private_archive_never_forwards_authorization_to_redirect(monkeypatch, tmp_path):
    calls = []
    def initial(url, **kw):
        assert kw['headers']['Authorization'] == 'Bearer host-secret'
        assert kw['allow_redirects'] is False
        return Response(302, {'Location': 'https://codeload.github.com/o/r/tar.gz/' + 'a'*40 + '?token=ephemeral'})
    class Session:
        trust_env = True
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, url, **kw):
            assert not self.trust_env
            assert 'headers' not in kw and kw['allow_redirects'] is False
            calls.append(url)
            return Response(200, content=tar_bytes())
    monkeypatch.setattr('githubapp.checkout.requests.get', initial)
    monkeypatch.setattr('githubapp.checkout.requests.Session', Session)
    path = acquire_archive(GitHubClient('host-secret'), 'o/r', 'a'*40, tmp_path, time.monotonic()+10, None)
    assert (path/'test_ok.py').is_file() and calls
    assert not any(b'host-secret' in f.read_bytes() for f in tmp_path.rglob('*') if f.is_file())


@pytest.mark.parametrize('location', ['https://evil.test/archive', 'http://codeload.github.com/x',
    'https://codeload.github.com@evil.test/x', 'https://codeload.github.com:443/x'])
def test_rejects_untrusted_archive_redirect(monkeypatch, tmp_path, location):
    monkeypatch.setattr('githubapp.checkout.requests.get', lambda *a, **kw: Response(302, {'Location': location}))
    with pytest.raises(GitHubClientError):
        acquire_archive(GitHubClient('secret'), 'o/r', 'a'*40, tmp_path, time.monotonic()+10, None)


def test_archive_retries_one_transient_read_and_removes_partial_download(monkeypatch, tmp_path):
    attempts = []
    def download(*args):
        attempts.append(1)
        if len(attempts) == 1:
            (tmp_path / "source.tar.gz").write_bytes(b"partial")
            raise requests.ReadTimeout()
        assert not (tmp_path / "source.tar.gz").exists()
        return tmp_path / "repo"
    monkeypatch.setattr("githubapp.checkout._acquire_archive_once", download)
    assert acquire_archive(GitHubClient("secret"), "o/r", "a"*40, tmp_path, time.monotonic()+10, None) == tmp_path / "repo"
    assert len(attempts) == 2


def test_archive_read_retries_are_bounded(monkeypatch, tmp_path):
    calls = []
    def download(*args):
        calls.append(1)
        raise requests.ReadTimeout()
    monkeypatch.setattr("githubapp.checkout._acquire_archive_once", download)
    with pytest.raises(GitHubClientError):
        acquire_archive(GitHubClient("secret"), "o/r", "a"*40, tmp_path, time.monotonic()+10, None)
    assert len(calls) == 2
