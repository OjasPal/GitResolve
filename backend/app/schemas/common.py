from pydantic import BaseModel, ConfigDict, model_validator
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    """Base for API models: camelCase JSON, snake_case Python."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class RequestModel(ApiModel):
    """Request bodies reject unknown fields (TRD section 5)."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid"
    )


class ResponseModel(ApiModel):
    """Responses are explicit allow-lists: unknown document fields are dropped."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="ignore"
    )


class PublicTaskModel(ResponseModel):
    """Sanitize task data on read, including legacy stored payloads."""

    @model_validator(mode="before")
    @classmethod
    def sanitize_public_payload(cls, value):
        from app.core.redaction import redact_value
        return redact_value(value)



class ErrorBody(ApiModel):
    code: str
    message: str
    details: dict | None = None


class ErrorResponse(ApiModel):
    error: ErrorBody
