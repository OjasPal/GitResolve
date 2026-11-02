import pytest

from tests.conftest import API, REPO_ID


def test_prompt_is_saved_and_planned_without_issue_lookup(env, monkeypatch):
    headers = env.headers()
    monkeypatch.setattr(env.github, "get_issue", lambda *args: pytest.fail("Unexpected issue lookup"))
    prompt = "Checkout crashes\nSubmit an empty cart. Expected: validation message."
    response = env.client.post(f"{API}/tasks", headers=headers, json={"repoId": REPO_ID, "prompt": prompt})
    assert response.status_code == 202, response.text
    task = response.json()
    assert task["issue"]["number"] is None
    assert task["issue"]["url"] is None
    assert task["issue"]["body"] == prompt
    assert env.run_worker()
    assert env.get(headers, task["id"])["status"] == "awaiting_start"


def test_prompt_idempotency(env):
    headers = {**env.headers(), "Idempotency-Key": "prompt-retry"}
    body = {"repoId": REPO_ID, "prompt": "  Fix checkout  "}
    first = env.client.post(f"{API}/tasks", headers=headers, json=body)
    replay = env.client.post(f"{API}/tasks", headers=headers, json=body)
    assert first.status_code == 202
    assert replay.status_code == 200
    assert first.json()["id"] == replay.json()["id"]
    changed = env.client.post(f"{API}/tasks", headers=headers, json={**body, "prompt": "Different bug"})
    assert changed.status_code == 409


@pytest.mark.parametrize("source", [{}, {"prompt": "   "}, {"prompt": "fix", "issueNumber": 1}, {"prompt": "x" * 20001}])
def test_invalid_sources(env, source):
    response = env.client.post(f"{API}/tasks", headers=env.headers(), json={"repoId": REPO_ID, **source})
    assert response.status_code == 400


def test_prompt_checks_repository_access(env):
    response = env.client.post(f"{API}/tasks", headers=env.headers(), json={"repoId": "999_999", "prompt": "Fix checkout"})
    assert response.status_code == 404


def test_direct_report_planning_intake_accepts_no_github_number():
    from agent.graph import analyze_issue
    from agent.llm import build_analysis_prompt
    state = {"repo_full_name": "owner/repo", "issue_number": None, "issue_title": "Fix checkout", "issue_body": "Empty cart crashes"}
    assert analyze_issue(state)["status"] == "planning"
    prompt = build_analysis_prompt("owner/repo", None, "Fix checkout", "Empty cart crashes")
    assert "Direct bug report" in prompt
    assert "#None" not in prompt
