"""Bounded public-repository acquisition and Docker-only patch/test execution."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time
import uuid

from sandbox.acquisition import acquire
from sandbox.container_entry import validate_patch
from sandbox.models import ExecutionRequest, ExecutionResult
from sandbox.process import SandboxError, capture, checkpoint

IMAGE = "gitresolve-sandbox:poc"
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean_output(raw: bytes | str, max_bytes: int) -> tuple[str, bool]:
    data = raw.encode() if isinstance(raw, str) else raw
    truncated = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8", errors="ignore")
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = CONTROL_CHARS.sub("", text)
    text = re.sub(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AIza[0-9A-Za-z_-]{30,}", "[REDACTED]", text)
    text = re.sub(r"(?i)(?:authorization|api[_-]?key|secret|password|token)\s*[:=]\s*[^\r\n]+", "[REDACTED]", text)
    # Replacement text can be longer than the original match.
    return text.encode()[:max_bytes].decode("utf-8", errors="ignore"), truncated


def _docker_run_args(container: str, repo_dir: Path, command: list[str], patch_path=None) -> list[str]:
    args = [
        "docker", "run", "-d", "--name", container,
        "--user", "10001:10001", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--memory", "2g", "--memory-swap", "2g", "--cpus", "2", "--pids-limit", "256",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m,uid=10001,gid=10001",
        "--tmpfs", "/workspace:rw,nosuid,nodev,size=1g,uid=10001,gid=10001",
        "--log-driver", "json-file", "--log-opt", "max-size=1m", "--log-opt", "max-file=1",
        "--mount", f"type=bind,source={repo_dir},target=/input,readonly",
        "-e", "HOME=/tmp", "--workdir", "/workspace",
    ]
    if patch_path:
        args += ["--mount", f"type=bind,source={patch_path},target=/patch.diff,readonly"]
    return args + [IMAGE, "python", "-I", "/usr/local/lib/gitresolve_entry.py", *command]


def _force_remove(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def run_sandbox(request: ExecutionRequest, allow_local_repo: bool = False, cancel=None) -> ExecutionResult:
    started = time.monotonic()
    deadline = started + request.timeout_seconds
    container = f"gitresolve-{request.execution_id}-{uuid.uuid4().hex[:8]}"
    workspace = None
    attempted = False
    result = ExecutionResult(execution_id=request.execution_id, status="error", commit_sha=request.commit_sha)
    try:
        checkpoint(deadline, cancel)
        if request.patch:
            validate_patch(request.patch)
        workspace = Path(tempfile.mkdtemp(prefix="gitresolve_"))
        repo = acquire(request, workspace, deadline, cancel, allow_local_repo)
        patch_path = None
        if request.patch:
            patch_path = workspace / "patch.diff"
            patch_path.write_text(request.patch, encoding="utf-8", newline="")
            patch_path.chmod(0o644)
        attempted = True
        start = capture(_docker_run_args(container, repo, request.command, patch_path), deadline, cancel, 8192)
        if start.returncode:
            raise SandboxError("docker_start_failed", "Docker could not start the sandbox.")
        while True:
            checkpoint(deadline, cancel)
            inspected = capture(["docker", "inspect", "-f", "{{json .State}}", container], min(deadline, time.monotonic() + 5), cancel, 8192)
            if inspected.returncode or inspected.stdout_truncated:
                raise SandboxError("docker_inspect_failed", "Docker could not inspect the sandbox.")
            state = json.loads(inspected.stdout)
            if not state["Running"]:
                break
            time.sleep(0.1)
        result.exit_code = state["ExitCode"]
        result.oom_killed = bool(state["OOMKilled"])
        logs = capture(["docker", "logs", container], deadline, cancel, request.max_output_bytes)
        if logs.returncode:
            raise SandboxError("logs_failed", "Execution logs could not be retrieved.")
        result.stdout, _ = _clean_output(logs.stdout, request.max_output_bytes)
        result.stderr, _ = _clean_output(logs.stderr, request.max_output_bytes)
        result.stdout_truncated = logs.stdout_truncated
        result.stderr_truncated = logs.stderr_truncated
        if result.oom_killed:
            result.error_code = "oom_killed"
            result.error_message = "Container exceeded its memory budget."
        elif result.exit_code == 125:
            result.error_code = "preparation_failed"
            result.error_message = "Workspace preparation or patch application failed."
        else:
            result.status = "passed" if result.exit_code == 0 else "failed"
    except SandboxError as exc:
        result.error_code, result.error_message = exc.code, exc.message
        if exc.code == "command_timeout":
            result.status, result.timed_out = "timed_out", True
    except ValueError:
        result.error_code, result.error_message = "invalid_input", "Patch or execution data is invalid."
    except OSError:
        result.error_code, result.error_message = "infrastructure_error", "Repository or Docker operation failed."
    except Exception:
        result.error_code, result.error_message = "internal_error", "Sandbox operation failed."
    finally:
        # Never hide cleanup failure behind a successful process exit.
        if attempted:
            try:
                removed = capture(["docker", "rm", "-f", container], time.monotonic() + 10, limit=4096)
                result.cleanup_ok = removed.returncode == 0
            except Exception:
                result.cleanup_ok = False
        if workspace is not None:
            try:
                shutil.rmtree(workspace, onexc=_force_remove)
            except OSError:
                result.cleanup_ok = False
        if not result.cleanup_ok:
            result.status = "error"
            result.error_code = "cleanup_failed"
            result.error_message = f"Cleanup failed for {container}; operator intervention required."
        result.duration_ms = int((time.monotonic() - started) * 1000)
    return result
