import abc
import json
import socket
from typing import Any, TypeVar

try:
    from anthropic import AnthropicVertex
    import anthropic
except ImportError:
    AnthropicVertex = None  # type: ignore[assignment, misc]
    anthropic = None  # type: ignore[assignment]

try:
    from google import genai
    from google.genai import types
    from google.genai import errors as genai_errors
except ImportError:
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]
    genai_errors = None  # type: ignore[assignment]
from pydantic import BaseModel

from agent.config import RouterSettings, get_router_settings
from agent.observability import UsageRecord, create_usage_record, sanitize_error

T = TypeVar("T", bound=BaseModel)


class ModelResult:
    def __init__(
        self,
        provider: str,
        model_id: str,
        parsed: Any | None = None,
        raw_text: str = "",
        usage: UsageRecord | None = None,
    ):
        self.provider = provider
        self.model_id = model_id
        self.parsed = parsed
        self.raw_text = raw_text
        self.usage = usage


class ProviderError(Exception):
    """Base exception for provider calls."""
    def __init__(self, message: str, provider: str, model_id: str, status_code: int | None = None, original_error: Exception | None = None):
        super().__init__(sanitize_error(message))
        self.provider = provider
        self.model_id = model_id
        self.status_code = status_code
        self.original_error = original_error


class RetryableProviderError(ProviderError):
    """Transient/retryable provider error (e.g. 429, 5xx, network timeout)."""
    pass


class QuotaExceededProviderError(RetryableProviderError):
    """Explicit quota exhaustion: switch provider without hammering the same quota."""


class FatalProviderError(ProviderError):
    """Non-retryable error (e.g. auth 401/403, bad request 400, invalid model)."""
    pass


def classify_error(exc: Exception, provider: str, model_id: str) -> ProviderError:
    """Classify an exception as either retryable or fatal."""
    status_code: int | None = None

    # Check Anthropic status code
    if hasattr(exc, "status_code") and isinstance(getattr(exc, "status_code"), int):
        status_code = exc.status_code
    # Check GenAI code
    elif hasattr(exc, "code") and isinstance(getattr(exc, "code"), int):
        status_code = exc.code

    if status_code == 429 and any(term in str(exc).lower() for term in ("quota exceeded", "quota_exceeded", "quota increase")):
        return QuotaExceededProviderError(
            "Cloud model quota exhausted. Check Vertex AI quotas for this model/project or use an available configured provider.",
            provider, model_id, status_code=429, original_error=exc)

    # Check for authentication / permission / config errors
    if anthropic is not None and isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return FatalProviderError(f"Authentication/Permission error: {exc}", provider, model_id, status_code=status_code or 401, original_error=exc)

    if anthropic is not None and isinstance(exc, anthropic.BadRequestError):
        return FatalProviderError(f"Bad request / invalid configuration: {exc}", provider, model_id, status_code=status_code or 400, original_error=exc)

    if "DefaultCredentialsError" in type(exc).__name__:
        return FatalProviderError(f"Missing Application Default Credentials: {exc}", provider, model_id, status_code=401, original_error=exc)

    if genai_errors is not None and isinstance(exc, genai_errors.ClientError):
        if status_code in (400, 401, 403, 404):
            return FatalProviderError(f"Client error ({status_code}): {exc}", provider, model_id, status_code=status_code, original_error=exc)

    # Check for retryable conditions
    if status_code in (429, 500, 502, 503, 504, 529):
        return RetryableProviderError(f"Transient HTTP error ({status_code}): {exc}", provider, model_id, status_code=status_code, original_error=exc)

    retryable_types: list[type[Exception]] = [TimeoutError, ConnectionError, socket.timeout]
    if anthropic is not None:
        retryable_types.extend(
            error_type for name in ("RateLimitError", "InternalServerError", "ServiceUnavailableError",
                "OverloadedError", "APIConnectionError", "APITimeoutError", "DeadlineExceededError")
            if isinstance((error_type := getattr(anthropic, name, None)), type)
        )
    if genai_errors is not None:
        retryable_types.append(genai_errors.ServerError)

    if isinstance(exc, tuple(retryable_types)):
        return RetryableProviderError(f"Transient network/service error: {exc}", provider, model_id, status_code=status_code, original_error=exc)

    # Check error message keywords for network interruptions or timeouts
    msg = str(exc).lower()
    if any(k in msg for k in ("connection reset", "connection refused", "timed out", "timeout", "econnreset", "socket hang up", "rate limit", "resource exhausted")):
        return RetryableProviderError(f"Transient error: {exc}", provider, model_id, status_code=status_code, original_error=exc)

    # Default to fatal provider error for unknown exceptions to avoid blind fallbacks
    return FatalProviderError(f"Provider error: {exc}", provider, model_id, status_code=status_code, original_error=exc)


class BaseProvider(abc.ABC):
    @abc.abstractmethod
    def generate(
        self,
        model_id: str,
        prompt: str,
        schema: type[T] | None = None,
        system_instruction: str | None = None,
        task_id: str | None = None,
    ) -> ModelResult:
        pass


class AnthropicVertexProvider(BaseProvider):
    def __init__(self, settings: RouterSettings | None = None):
        self.settings = settings or get_router_settings()
        self.project_id = self.settings.anthropic_vertex_project_id
        self.region = self.settings.anthropic_vertex_region
        self._client: AnthropicVertex | None = None

    def _get_client(self) -> AnthropicVertex:
        if AnthropicVertex is None:
            raise FatalProviderError(
                "anthropic package with vertex extra is not installed. Install with: uv add 'anthropic[vertex]>=2.3.0'",
                provider="anthropic",
                model_id="",
            )
        if self._client is None:
            if not self.project_id:
                raise FatalProviderError(
                    "ANTHROPIC_VERTEX_PROJECT_ID or GOOGLE_CLOUD_PROJECT must be configured.",
                    provider="anthropic",
                    model_id="",
                )
            self._client = AnthropicVertex(
                project_id=self.project_id,
                region=self.region,
                timeout=self.settings.request_timeout_seconds,
                max_retries=0,
            )
        return self._client

    def generate(
        self,
        model_id: str,
        prompt: str,
        schema: type[T] | None = None,
        system_instruction: str | None = None,
        task_id: str | None = None,
    ) -> ModelResult:
        try:
            client = self._get_client()

            messages = [{"role": "user", "content": prompt}]
            kwargs: dict[str, Any] = {
                "model": model_id,
                "max_tokens": self.settings.max_output_tokens,
                "messages": messages,
            }
            if system_instruction:
                kwargs["system"] = system_instruction

            if schema is not None:
                # Use client.messages.parse for Pydantic structured output
                response = client.messages.parse(
                    output_format=schema,
                    **kwargs,
                )
                parsed = response.parsed
                raw_text = ""
                # Attempt to extract text from content blocks if available
                if hasattr(response, "content") and response.content:
                    raw_text = "\n".join(
                        getattr(b, "text", "") for b in response.content if hasattr(b, "text")
                    )
            else:
                response = client.messages.create(**kwargs)
                parsed = None
                raw_text = ""
                if hasattr(response, "content") and response.content:
                    raw_text = "\n".join(
                        getattr(b, "text", "") for b in response.content if hasattr(b, "text")
                    )

            # Extract usage
            input_tokens = None
            output_tokens = None
            cached_tokens = None
            if hasattr(response, "usage") and response.usage:
                input_tokens = getattr(response.usage, "input_tokens", None)
                output_tokens = getattr(response.usage, "output_tokens", None)
                cached_tokens = getattr(response.usage, "cache_read_input_tokens", None)

            usage = create_usage_record(
                provider="anthropic",
                model_id=model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                task_id=task_id,
            )

            return ModelResult(
                provider="anthropic",
                model_id=model_id,
                parsed=parsed,
                raw_text=raw_text,
                usage=usage,
            )
        except Exception as exc:
            if isinstance(exc, ProviderError):
                raise
            raise classify_error(exc, provider="anthropic", model_id=model_id) from exc


class GoogleGenAIProvider(BaseProvider):
    def __init__(self, settings: RouterSettings | None = None):
        self.settings = settings or get_router_settings()
        self.project = self.settings.google_cloud_project
        self.location = self.settings.google_cloud_location
        self.enterprise = self.settings.google_genai_use_enterprise
        self._client: genai.Client | None = None

    def _get_client(self) -> genai.Client:
        if self._client is None:
            if not self.project:
                raise FatalProviderError(
                    "GOOGLE_CLOUD_PROJECT must be configured.",
                    provider="google",
                    model_id="",
                )
            self._client = genai.Client(
                enterprise=self.enterprise,
                project=self.project,
                location=self.location,
                http_options=types.HttpOptions(timeout=int(self.settings.request_timeout_seconds * 1000), retry_options=types.HttpRetryOptions(attempts=1)),
            )
        return self._client

    def generate(
        self,
        model_id: str,
        prompt: str,
        schema: type[T] | None = None,
        system_instruction: str | None = None,
        task_id: str | None = None,
    ) -> ModelResult:
        try:
            client = self._get_client()

            config_kwargs: dict[str, Any] = {"max_output_tokens": self.settings.max_output_tokens}
            if model_id.startswith(("gemini-3", "gemini-4")):
                config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=self.settings.thinking_level)
            if system_instruction:
                config_kwargs["system_instruction"] = system_instruction

            if schema is not None:
                config_kwargs["response_mime_type"] = "application/json"
                config_kwargs["response_schema"] = schema

            config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None

            response = client.models.generate_content(
                model=model_id,
                contents=prompt,
                config=config,
            )

            parsed = None
            if schema is not None:
                if hasattr(response, "parsed") and isinstance(response.parsed, schema):
                    parsed = response.parsed
                elif response.text:
                    parsed = schema.model_validate_json(response.text)

            input_tokens = None
            output_tokens = None
            cached_tokens = None
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                input_tokens = getattr(response.usage_metadata, "prompt_token_count", None)
                output_tokens = getattr(response.usage_metadata, "candidates_token_count", None)
                cached_tokens = getattr(response.usage_metadata, "cached_content_token_count", None)

            usage = create_usage_record(
                provider="google",
                model_id=model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                task_id=task_id,
            )

            return ModelResult(
                provider="google",
                model_id=model_id,
                parsed=parsed,
                raw_text=getattr(response, "text", "") or "",
                usage=usage,
            )
        except Exception as exc:
            if isinstance(exc, ProviderError):
                raise
            raise classify_error(exc, provider="google", model_id=model_id) from exc
