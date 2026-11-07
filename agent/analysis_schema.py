from typing import Any, Literal
from pydantic import BaseModel, Field


class IssueAnalysis(BaseModel):
    """Structured output expected from the issue-analysis LLM."""

    task_title: str = Field(
        default="",
        max_length=100,
        description="A short action title, 5 to 10 words, describing the requested change. Never copy the full prompt.",
    )

    problem_summary: str = Field(
        description="A concise summary of the reported bug, feature, refactor, or config change."
    )

    investigation_steps: list[str] = Field(
        min_length=1,
        max_length=5,
        description=(
            "Ordered investigation or implementation steps to address the issue. "
            "Do not assume unverified root causes."
        ),
    )

    needs_more_information: bool = Field(
        description=(
            "True if the issue lacks enough information to begin "
            "a meaningful investigation."
        )
    )

    questions_for_reporter: list[str] = Field(
        description=(
            "Specific questions for the issue reporter. "
            "Use an empty list if none are needed."
        )
    )

    task_type: Literal["bug", "feature", "refactor", "config", "unclear"] = Field(
        default="bug",
        description="Task classification: bug, feature, refactor, config, or unclear.",
    )

    spec: str = Field(
        default="",
        max_length=2000,
        description="Concise technical specification for the requested change.",
    )

    acceptance_criteria: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Concrete acceptance criteria to verify the implementation.",
    )

    expected_files: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Repository-relative file paths expected to be modified.",
    )

    line_budget: int | None = Field(
        default=None,
        ge=1,
        le=1000,
        description="Estimated diff line budget for the change.",
    )

    dependency_changes: list[dict[str, Any]] = Field(
        default_factory=list,
        max_length=10,
        description="Required exact package==version dependency pins with reasons, if any.",
    )
