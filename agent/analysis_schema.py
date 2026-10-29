from pydantic import BaseModel, Field


class IssueAnalysis(BaseModel):
    """Structured output expected from the issue-analysis LLM."""

    problem_summary: str = Field(
        description="A concise summary of the reported bug."
    )

    investigation_steps: list[str] = Field(
        min_length=1,
        max_length=5,
        description=(
            "Ordered investigation steps to determine the root cause. "
            "Do not assume the cause is already known."
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
