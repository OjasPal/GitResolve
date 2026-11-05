import io
import json
import sys
import tarfile
import time

import pytest

from sandbox import acquisition, runner
from sandbox.container_entry import validate_patch
from sandbox.models import ExecutionRequest
from sandbox.process import Captured, SandboxError, capture


def archive(name="root/app.py", kind=tarfile.REGTYPE, data=b"pass\n"):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as tar:
        info = tarfile.TarInfo(name)
        info.type = kind
        info.linkname = "/etc/passwd"
        info.size = len(data) if kind == tarfile.REGTYPE else 0
        tar.addfile(info, io.BytesIO(data) if info.size else None)
    stream.seek(0)
    return stream


@pytest.mark.parametrize("name,kind", [
    ("root/../../escape", tarfile.REGTYPE), ("/root/file", tarfile.REGTYPE),
    ("root/link", tarfile.SYMTYPE), ("root/hard", tarfile.LNKTYPE),
    ("root/dev", tarfile.CHRTYPE), ("root/.GIT/config", tarfile.REGTYPE),
    ("root/C:/escape", tarfile.REGTYPE), ("root/NUL", tarfile.REGTYPE),
])
def test_archive_rejects_escape_and_special_files(tmp_path, name, kind):
    with pytest.raises(SandboxError, match="Unsafe archive"):
        acquisition.extract_archive(archive(name, kind), tmp_path / "repo", time.monotonic() + 5, None)


def test_archive_size_and_file_limits_apply_before_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(acquisition, "EXPANDED_LIMIT", 3)
    with pytest.raises(SandboxError):
        acquisition.extract_archive(archive(), tmp_path / "repo", time.monotonic() + 5, None)
    assert not (tmp_path / "repo/app.py").exists()


def test_archive_cancellation(tmp_path):
    with pytest.raises(SandboxError) as exc:
        acquisition.extract_archive(archive(), tmp_path / "repo", time.monotonic() + 5, lambda: True)
    assert exc.value.code == "cancelled"


def test_output_capture_is_bounded_across_both_streams():
    result = capture([sys.executable, "-c", "import sys; print('a'*100000); sys.stderr.write('b'*100000)"],
                     time.monotonic() + 10, limit=2000)
    assert len(result.stdout) + len(result.stderr) <= 2000
    assert result.stdout_truncated or result.stderr_truncated


def test_subprocess_is_terminated_on_timeout():
    with pytest.raises(SandboxError) as exc:
        capture([sys.executable, "-c", "import time; time.sleep(60)"], time.monotonic() + 0.2)
    assert exc.value.code == "command_timeout"


@pytest.mark.parametrize("failure", ["none", "inspect", "logs", "cleanup", "cancel", "oom"])
def test_container_cleanup_on_every_path(tmp_path, monkeypatch, failure):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(runner, "acquire", lambda *args: repo)
    calls = []
    def fake_capture(args, *a, **kw):
        calls.append(args)
        if args[1] == "inspect":
            return Captured(1 if failure == "inspect" else 0,
                            json.dumps({"Running": False, "ExitCode": 0, "OOMKilled": failure == "oom"}).encode(), b"", False, False)
        if args[1] == "logs":
            return Captured(1 if failure == "logs" else 0, b"ok", b"", False, False)
        return Captured(1 if args[1] == "rm" and failure == "cleanup" else 0, b"", b"", False, False)
    monkeypatch.setattr(runner, "capture", fake_capture)
    request = ExecutionRequest(execution_id="x", repo_url="https://github.com/a/b", commit_sha="a" * 40, command=["python"])
    result = runner.run_sandbox(request, cancel=lambda: failure == "cancel" and bool(calls))
    assert any(c[1:3] == ["rm", "-f"] for c in calls)
    assert result.status == ("passed" if failure == "none" else "error")
    assert result.cleanup_ok == (failure != "cleanup")
    if failure == "oom":
        assert result.oom_killed and result.error_code == "oom_killed"


@pytest.mark.parametrize("path", ["../x", ".git/config", ".env", "pyproject.toml", "x.lock"])
def test_patch_protected_paths(path):
    with pytest.raises(ValueError):
        validate_patch(f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-old\n+new\n")


def test_patch_regular_file():
    assert validate_patch("--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n") == ["app.py"]
