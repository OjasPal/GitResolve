import subprocess

import pytest
from pydantic import ValidationError

from sandbox.models import ExecutionRequest
from sandbox.runner import IMAGE, _clean_output, run_sandbox

PYTEST = ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]


def _docker_ready() -> bool:
    try:
        result = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, timeout=20)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


needs_docker = pytest.mark.skipif(not _docker_ready(), reason="Docker not running or image not built")


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def fixture_repo(tmp_path):
    repo = tmp_path / "fixture_repo"
    repo.mkdir()
    (repo / "test_pass.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n")
    (repo / "test_fail.py").write_text("def test_bad():\n    assert 1 + 1 == 3\n")
    (repo / "test_hang.py").write_text("import time\n\ndef test_hang():\n    time.sleep(600)\n")
    _git(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "fixture")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    return str(repo), sha


def _request(fixture_repo, command, **kwargs):
    url, sha = fixture_repo
    return ExecutionRequest(execution_id="test-run", repo_url=url, commit_sha=sha, command=command, **kwargs)


def _leftover_containers():
    out = subprocess.run(
        ["docker", "ps", "-a", "-q", "--filter", "name=gitresolve-"], capture_output=True, text=True
    ).stdout
    return out.split()


# ---- Unit tests: no Docker needed ----

def test_rejects_bad_commit_sha():
    with pytest.raises(ValidationError):
        ExecutionRequest(execution_id="x", repo_url="https://github.com/a/b", commit_sha="abc", command=["ls"])


def test_rejects_option_like_repo_url():
    with pytest.raises(ValidationError):
        ExecutionRequest(execution_id="x", repo_url="--upload-pack=evil", commit_sha="a" * 40, command=["ls"])


def test_rejects_unsafe_execution_id():
    with pytest.raises(ValidationError):
        ExecutionRequest(execution_id="a;rm -rf", repo_url="https://github.com/a/b", commit_sha="a" * 40, command=["ls"])


def test_non_github_url_is_blocked_by_default(fixture_repo):
    result = run_sandbox(_request(fixture_repo, PYTEST))  # allow_local_repo defaults to False
    assert result.status == "error"
    assert result.error_code == "invalid_repo_url"


def test_nonexistent_commit_is_an_error(fixture_repo):
    url, _ = fixture_repo
    request = ExecutionRequest(
        execution_id="test-run", repo_url=url, commit_sha="0" * 40, command=PYTEST + ["test_pass.py"]
    )
    result = run_sandbox(request, allow_local_repo=True)
    assert result.status == "error"
    assert result.error_code == "checkout_failed"


def test_clean_output_strips_control_chars_and_truncates():
    text, truncated = _clean_output("a\x00b\x1bc", 100)
    assert text == "abc" and not truncated
    text, truncated = _clean_output("x" * 500, 100)
    assert len(text.encode()) == 100 and truncated


# ---- Docker tests: need Docker running and the image built ----

@needs_docker
def test_passing_tests(fixture_repo):
    result = run_sandbox(_request(fixture_repo, PYTEST + ["test_pass.py"]), allow_local_repo=True)
    assert result.status == "passed"
    assert result.exit_code == 0


@needs_docker
def test_failing_tests(fixture_repo):
    result = run_sandbox(_request(fixture_repo, PYTEST + ["test_fail.py"]), allow_local_repo=True)
    assert result.status == "failed"
    assert result.exit_code != 0
    assert not result.timed_out


@needs_docker
def test_timeout_kills_and_cleans_up(fixture_repo):
    before = set(_leftover_containers())
    result = run_sandbox(
        _request(fixture_repo, PYTEST + ["test_hang.py"], timeout_seconds=5), allow_local_repo=True
    )
    assert result.status == "timed_out"
    assert result.timed_out
    assert result.cleanup_ok
    assert set(_leftover_containers()) <= before


@needs_docker
def test_large_output_is_truncated(fixture_repo):
    url, sha = fixture_repo
    request = ExecutionRequest(
        execution_id="test-run",
        repo_url=url,
        commit_sha=sha,
        command=["python", "-c", "import sys; sys.stdout.buffer.write(b'x' * 15_000_000)"],
        max_output_bytes=10_000,
    )
    result = run_sandbox(request, allow_local_repo=True)
    assert result.stdout_truncated
    assert len(result.stdout.encode()) <= 10_000


@needs_docker
def test_runs_as_non_root(fixture_repo):
    command = ["python", "-c", "import os, sys; sys.exit(0 if os.getuid() == 10001 else 1)"]
    result = run_sandbox(_request(fixture_repo, command), allow_local_repo=True)
    assert result.status == "passed"


@needs_docker
def test_network_is_blocked(fixture_repo):
    command = ["python", "-c", "import urllib.request; urllib.request.urlopen('http://example.com', timeout=3)"]
    result = run_sandbox(_request(fixture_repo, command), allow_local_repo=True)
    assert result.status == "failed"
    assert "URLError" in result.stderr


@needs_docker
def test_patch_is_applied_only_inside_container(fixture_repo):
    from pathlib import Path
    patch = "--- a/test_fail.py\n+++ b/test_fail.py\n@@ -1,2 +1,2 @@\n def test_bad():\n-    assert 1 + 1 == 3\n+    assert 1 + 1 == 2\n"
    result = run_sandbox(_request(fixture_repo, PYTEST + ["test_fail.py"], patch=patch), allow_local_repo=True)
    assert result.status == "passed" and result.cleanup_ok
    assert "== 3" in (Path(fixture_repo[0]) / "test_fail.py").read_text()


@needs_docker
def test_mount_permissions_and_storage_bound(fixture_repo):
    script = """import os
from pathlib import Path
assert os.getuid() == 10001
Path('/workspace/writable').write_text('yes')
for path in ('/input/newfile', '/usr/newfile'):
    try:
        Path(path).write_text('no')
    except OSError:
        pass
    else:
        raise AssertionError(path)
space = os.statvfs('/workspace')
assert space.f_blocks * space.f_frsize <= 1024 ** 3
status = Path('/proc/self/status').read_text()
assert 'NoNewPrivs:\\t1' in status
assert 'CapEff:\\t0000000000000000' in status
"""
    result = run_sandbox(_request(fixture_repo, ["python", "-c", script]), allow_local_repo=True)
    assert result.status == "passed", result.model_dump()


@needs_docker
def test_cancellation_cleans_container(fixture_repo):
    import time
    started = time.monotonic()
    result = run_sandbox(_request(fixture_repo, PYTEST + ["test_hang.py"]), allow_local_repo=True,
                         cancel=lambda: time.monotonic() - started > 3)
    assert result.error_code == "cancelled" and result.cleanup_ok
