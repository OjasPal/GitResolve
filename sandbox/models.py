"""Request/response models for the sandbox runner."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    execution_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    repo_url: str = Field(min_length=1, max_length=500)
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    command: list[str] = Field(min_length=1, max_length=20)
    timeout_seconds: int = Field(default=60, ge=1, le=300)
    max_output_bytes: int = Field(default=1_000_000, ge=1_000, le=1_048_576)
    patch: str = Field(default="", max_length=100_000)

    @field_validator("repo_url")
    @classmethod
    def _check_repo_url(cls, value: str) -> str:
        # A leading "-" could be mistaken for a git option.
        if value.startswith("-") or "\x00" in value or "\n" in value:
            raise ValueError("repo_url contains forbidden characters")
        return value

    @field_validator("command")
    @classmethod
    def _check_command(cls, value: list[str]) -> list[str]:
        if not value[0].strip():
            raise ValueError("command must start with a program name")
        for arg in value:
            if "\x00" in arg or len(arg) > 500:
                raise ValueError("command argument is invalid")
        return value


class ExecutionResult(BaseModel):
    execution_id: str
    status: Literal["passed", "failed", "timed_out", "error"]
    exit_code: int | None = None
    timed_out: bool = False
    oom_killed: bool = False
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    duration_ms: int = 0
    commit_sha: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    cleanup_ok: bool = True
    # Rotation happens before capture, so capture flags cannot prove completeness.
    logs_complete: bool = False
