"""Minimal Docker execution runner (proof of concept).

Runs a test command for one exact commit inside a locked-down container.
Never call this from inside the container itself, and never run repository
code in the API or agent process.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
import json
from pathlib import Path

from sandbox.models import ExecutionRequest, ExecutionResult

IMAGE = "gitresolve-sandbox:poc"
GITHUB_URL = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?(\.git)?$")
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
GIT_TIMEOUT_SECONDS = 120
DOCKER_TIMEOUT_SECONDS = 30


class SandboxError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _run(cmd: list[str], timeout: int, env: dict | None = None) -> subprocess.CompletedProcess:
    # List arguments + no shell=True: nothing is interpreted by a host shell.
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        check=False,
    )


def _run_bytes(cmd: list[str], timeout: int, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        timeout=timeout,
        env=env,
        check=False,
    )


def _clip(text: str, limit: int = 500) -> str:
    text = CONTROL_CHARS.sub("", text).strip()
    return text[:limit]


def _clean_output(raw: bytes | str, max_bytes: int) -> tuple[str, bool]:
    """Cap byte size first, then decode and strip control characters. Returns (text, was_truncated)."""
    if isinstance(raw, str):
        data = raw.encode("utf-8", errors="replace")
    else:
        data = raw

    was_truncated = len(data) > max_bytes
    if was_truncated:
        data = data[:max_bytes]

    text = data.decode("utf-8", errors="ignore")
    text = CONTROL_CHARS.sub("", text)
    return text, was_truncated


def _checkout(request: ExecutionRequest, workspace: Path, allow_local_repo: bool) -> Path:
    url = request.repo_url
    is_github = bool(GITHUB_URL.match(url))
    is_local = allow_local_repo and Path(url).is_dir()
    if not (is_github or is_local):
        raise SandboxError("invalid_repo_url", "Repository URL is not allowed.")

    repo_dir = workspace / "repo"
    env = {k: v for k, v in os.environ.items() if k.upper() in {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT"
    }}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_COUNT="2",
               GIT_CONFIG_KEY_0="core.hooksPath", GIT_CONFIG_VALUE_0=str(workspace / "no-hooks"),
               GIT_CONFIG_KEY_1="credential.helper", GIT_CONFIG_VALUE_1="")

    clone = _run(["git", "clone", "--no-local", "--no-checkout", "--", url, str(repo_dir)], GIT_TIMEOUT_SECONDS, env)
    if clone.returncode != 0:
        raise SandboxError("clone_failed", _clip(clone.stderr))

    checkout = _run(
        ["git", "-C", str(repo_dir), "checkout", "--detach", request.commit_sha],
        GIT_TIMEOUT_SECONDS,
        env,
    )
    if checkout.returncode != 0:
        raise SandboxError("checkout_failed", _clip(checkout.stderr))

    head = _run(["git", "-C", str(repo_dir), "rev-parse", "HEAD"], GIT_TIMEOUT_SECONDS, env)
    if head.stdout.strip() != request.commit_sha:
        raise SandboxError("sha_mismatch", "Checked-out commit does not match the request.")
    total = 0
    for path in repo_dir.rglob("*"):
        if path.is_symlink():
            raise SandboxError("unsafe_path", "Repository symlinks are not supported.")
        if path.is_file():
            total += path.stat().st_size
            if total > 100_000_000:
                raise SandboxError("repository_too_large", "Repository exceeds the checkout limit.")
    return repo_dir


def _docker_run_args(container: str, repo_dir: Path, command: list[str]) -> list[str]:
    return [
        "docker", "run", "-d",
        "--name", container,
        "--user", "10001:10001",          # non-root
        "--network", "none",              # no network while tests run
        "--read-only",                    # root filesystem is read-only
        "--cap-drop", "ALL",              # drop all Linux capabilities
        "--security-opt", "no-new-privileges",
        "--memory", "2g", "--memory-swap", "2g",
        "--cpus", "2",
        "--pids-limit", "256",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m,uid=10001,gid=10001",
        "--tmpfs", "/workspace:rw,nosuid,nodev,size=1g,uid=10001,gid=10001",
        "--log-driver", "json-file",      # cap host-side log size
        "--log-opt", "max-size=20m", "--log-opt", "max-file=1",
        "--mount", f"type=bind,source={repo_dir},target=/input,readonly",
        "-e", "HOME=/tmp",                # only fixed values; no host secrets
        "--workdir", "/workspace",
        IMAGE,
        "python", "-c",
        "import os,shutil,sys; shutil.copytree('/input','/workspace',dirs_exist_ok=True,"
        "ignore=shutil.ignore_patterns('.git')); os.execvp(sys.argv[1],sys.argv[1:])",
        *command,
    ]


def _error_result(request: ExecutionRequest, started: float, code: str, message: str) -> ExecutionResult:
    return ExecutionResult(
        execution_id=request.execution_id,
        status="error",
        commit_sha=request.commit_sha,
        duration_ms=int((time.monotonic() - started) * 1000),
        error_code=code,
        error_message=message,
    )


def _force_remove(func, path, _exc) -> None:
    # Git marks some files read-only on Windows; make them writable and retry.
    os.chmod(path, stat.S_IWRITE)
    func(path)


def run_sandbox(request: ExecutionRequest, allow_local_repo: bool = False, cancel=None) -> ExecutionResult:
    """Run request.command against request.commit_sha inside Docker.

    allow_local_repo exists only for tests with a local fixture repo. It is a
    function argument (not part of the request) so a caller cannot enable it
    through request data.
    """
    started = time.monotonic()
    container = f"gitresolve-{request.execution_id}-{uuid.uuid4().hex[:8]}"
    workspace = Path(tempfile.mkdtemp(prefix="gitresolve_"))
    result = None
    started_container = False

    try:
        repo_dir = _checkout(request, workspace, allow_local_repo)
        if cancel and cancel():
            raise SandboxError("cancelled", "Execution cancelled before container start.")

        start = _run(_docker_run_args(container, repo_dir, request.command), DOCKER_TIMEOUT_SECONDS)
        if start.returncode != 0:
            raise SandboxError("docker_start_failed", _clip(start.stderr))
        started_container = True

        timed_out = False
        exit_code: int | None = None
        try:
            deadline = time.monotonic() + request.timeout_seconds
            while True:
                if cancel and cancel():
                    raise SandboxError("cancelled", "Execution cancelled.")
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired("sandbox", request.timeout_seconds)
                state = _run(["docker", "inspect", "-f", "{{json .State}}", container], 5)
                if state.returncode:
                    raise SandboxError("docker_inspect_failed", "Cannot inspect execution.")
                status_doc = json.loads(state.stdout)
                if not status_doc["Running"]:
                    exit_code = status_doc["ExitCode"]
                    break
                time.sleep(0.1)
        except subprocess.TimeoutExpired:
            timed_out = True
            _run(["docker", "kill", container], DOCKER_TIMEOUT_SECONDS)

        logs = _run_bytes(["docker", "logs", container], DOCKER_TIMEOUT_SECONDS)
        stdout, out_truncated = _clean_output(logs.stdout, request.max_output_bytes)
        stderr, err_truncated = _clean_output(logs.stderr, request.max_output_bytes)

        inspect = _run(["docker", "inspect", "-f", "{{.State.OOMKilled}}", container], DOCKER_TIMEOUT_SECONDS)
        oom_killed = inspect.stdout.strip() == "true"

        if timed_out:
            status = "timed_out"
        elif exit_code == 0:
            status = "passed"
        else:
            status = "failed"

        result = ExecutionResult(
            execution_id=request.execution_id,
            status=status,
            exit_code=exit_code,
            timed_out=timed_out,
            oom_killed=oom_killed,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=out_truncated,
            stderr_truncated=err_truncated,
            duration_ms=int((time.monotonic() - started) * 1000),
            commit_sha=request.commit_sha,
        )
        return result

    except SandboxError as exc:
        return _error_result(request, started, exc.code, exc.message)
    except subprocess.TimeoutExpired:
        return _error_result(request, started, "command_timeout", "A git or docker command took too long.")
    except OSError:
        return _error_result(request, started, "tool_not_found", "git or docker could not be started.")
    except Exception as exc:  # last resort: report instead of crashing the caller
        return _error_result(request, started, "internal_error", f"Unexpected error: {type(exc).__name__}")
    finally:
        # Cleanup runs on success, failure and timeout alike.
        try:
            removed = _run(["docker", "rm", "-f", container], DOCKER_TIMEOUT_SECONDS)
            if started_container and removed.returncode and result is not None:
                result.status = "error"
                result.error_code = "cleanup_failed"
                result.error_message = "Container cleanup failed. Operator intervention required."
        except (OSError, subprocess.TimeoutExpired):
            if result is not None:
                result.status = "error"
                result.error_code = "cleanup_failed"
        try:
            shutil.rmtree(workspace, onexc=_force_remove)
        except OSError:
            if result is not None:
                result.status = "error"
                result.error_code = "cleanup_failed"
