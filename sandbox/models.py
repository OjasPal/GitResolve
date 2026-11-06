"""Request/response models for the sandbox runner."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    execution_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    repo_url: str = Field(min_length=1, max_length=500)
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    command: list[str] = Field(min_length=1, max_length=20)
    timeout_seconds: int = Field(default=60, ge=1, le=300)
    max_output_bytes: int = Field(default=1_000_000, ge=1_000, le=1_048_576)
    patch: str = Field(default="", max_length=100_000)
    max_diff_lines: int = Field(default=150, ge=1, le=400)
    prepare_dependencies: bool = False
    profile: Literal["python", "streamlit"] = "python"
    ui_entrypoint: str = Field(default="", max_length=200)
    require_ui_checks: bool = False
    require_ui_interaction: bool = False
    require_ui_report: bool = False
    require_ui_answers: bool = False
    allowed_deletions: list[str] = Field(default_factory=list, max_length=4)
    verify_cleanup: bool = False

    @field_validator("ui_entrypoint")
    @classmethod
    def safe_entrypoint(cls, value):
        if value and (value.startswith("/") or ".." in value.split("/") or any(c in value for c in "\\:\r\n\x00") or not value.endswith(".py")):
            raise ValueError("Invalid Streamlit entry point.")
        return value

    @model_validator(mode="after")
    def approved_task_scope(self):
        if self.profile == "streamlit" and not self.ui_entrypoint:
            raise ValueError("Streamlit verification requires an entry point.")
        if self.require_ui_checks and self.profile != "streamlit":
            raise ValueError("UI verification requires the Streamlit profile.")
        if self.allowed_deletions:
            from sandbox.cleanup import safe_names
            safe_names(self.allowed_deletions)
            if self.patch:
                from sandbox.container_entry import validate_patch
                validate_patch(self.patch,self.max_diff_lines,self.allowed_deletions)
        if self.verify_cleanup and not (self.allowed_deletions and self.patch):
            raise ValueError("Cleanup verification requires approved deletions and a patch.")
        if self.max_diff_lines > 150 and not self.allowed_deletions:
            import json
            from pathlib import Path
            grants_file = Path(__file__).resolve().parents[1] / ".diagnostics" / "task_scope_grants.json"
            try:
                grant = json.loads(grants_file.read_text(encoding="utf-8")).get(self.execution_id, {})
            except (OSError, ValueError):
                grant = {}
            if grant.get("repo_url") != self.repo_url or grant.get("max_diff_lines") != self.max_diff_lines:
                raise ValueError("Expanded change budget requires an explicit local grant for this exact task and repository.")
        return self

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
