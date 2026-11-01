import pytest

from githubapp import client as client_module
from githubapp.client import GitHubClient, GitHubClientError


class FakeResponse:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data or {}

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http error")


def test_resolve_branch_sha(monkeypatch):
    monkeypatch.setattr(client_module.requests, "get", lambda *a, **k: FakeResponse(200, {"commit": {"sha": "a" * 40}}))
    assert GitHubClient("t").resolve_branch_sha("o", "r", "main") == "a" * 40


def test_not_found(monkeypatch):
    monkeypatch.setattr(client_module.requests, "get", lambda *a, **k: FakeResponse(404))
    with pytest.raises(GitHubClientError):
        GitHubClient("t").get_repo("o", "r")


def test_permission_denied(monkeypatch):
    monkeypatch.setattr(client_module.requests, "get", lambda *a, **k: FakeResponse(403))
    with pytest.raises(GitHubClientError):
        GitHubClient("t").get_repo("o", "r")


def test_rejects_bad_names():
    with pytest.raises(GitHubClientError):
        GitHubClient("t").get_repo("o/../x", "r")


def test_requires_token():
    with pytest.raises(ValueError):
        GitHubClient("")