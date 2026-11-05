import abc
import json
import socket
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, TypeVar

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
    def __init__(self, message: str, provider: str, model_id: str, status_code: int | None = None, original_error: Exception | None = None, retry_after: float | None = None):
        super().__init__(sanitize_error(message))
        self.provider = provider
        self.model_id = model_id
        self.status_code = status_code
        self.original_error = original_error
        self.retry_after = retry_after


class RetryableProviderError(ProviderError):
    """Transient/retryable provider error (e.g. 429, 5xx, network timeout)."""
    pass


class QuotaExceededProviderError(RetryableProviderError):
    """Explicit quota exhaustion: switch provider without hammering the same quota."""


class FatalProviderError(ProviderError):
    """Non-retryable error (e.g. auth 401/403, bad request 400, invalid model)."""
    pass


def _classify_error(exc: Exception, provider: str, model_id: str) -> ProviderError:
    """Classify an exception as either retryable or fatal."""
    status_code: int | None = None

    # Read HTTP status code
    if hasattr(exc, "status_code") and isinstance(getattr(exc, "status_code"), int):
        status_code = exc.status_code
    # Check GenAI code
    elif hasattr(exc, "code") and isinstance(getattr(exc, "code"), int):
        status_code = exc.code

    if status_code == 429 and any(term in str(exc).lower() for term in ("quota exceeded", "quota_exceeded", "quota increase")):
        return QuotaExceededProviderError(
            "Cloud model quota exhausted. Check Vertex AI quotas for this model/project or use an available configured provider.",
            provider, model_id, status_code=429, original_error=exc)

    if "DefaultCredentialsError" in type(exc).__name__:
        return FatalProviderError(f"Missing Application Default Credentials: {exc}", provider, model_id, status_code=401, original_error=exc)

    if genai_errors is not None and isinstance(exc, genai_errors.ClientError):
        if status_code in (400, 401, 403, 404):
            return FatalProviderError(f"Client error ({status_code}): {exc}", provider, model_id, status_code=status_code, original_error=exc)

    # Check for retryable conditions
    if status_code in (408, 429, 500, 502, 503, 504, 529):
        return RetryableProviderError(f"Transient HTTP error ({status_code}): {exc}", provider, model_id, status_code=status_code, original_error=exc)

    retryable_types: list[type[Exception]] = [TimeoutError, ConnectionError, socket.timeout]
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


def classify_error(exc, provider, model_id):
    classified = _classify_error(exc, provider, model_id)
    headers = getattr(getattr(exc, "response", None), "headers", None) or getattr(exc, "headers", {}) or {}
    hint = headers.get("Retry-After") or headers.get("retry-after")
    try:
        classified.retry_after = max(0, float(hint))
    except (TypeError, ValueError):
        try:
            classified.retry_after = max(0, (parsedate_to_datetime(hint) - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            # Google RPC RetryInfo is sometimes returned instead of a header.
            details = (getattr(exc, "response_json", {}) or {}).get("error", {}).get("details", [])
            for detail in details:
                if "RetryInfo" in detail.get("@type", ""):
                    try: classified.retry_after = max(0, float(detail["retryDelay"].rstrip("s")))
                    except (KeyError, TypeError, ValueError): pass
    return classified


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


class ImageContextPrompt(str):
    """Text plus already validated image bytes; no paths or URLs from uploads."""
    def __new__(cls, text: str, images: list[bytes]):
        obj = super().__new__(cls, text)
        obj.images = images
        return obj


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

            config_kwargs: dict[str, Any] = {"max_output_tokens": self.settings.max_output_tokens,
                "http_options": types.HttpOptions(timeout=int((self.settings.specialist_timeout_seconds if model_id == self.settings.specialist.model_id else self.settings.request_timeout_seconds) * 1000), retry_options=types.HttpRetryOptions(attempts=1))}
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
                contents=([types.Part.from_text(text=str(prompt))] + [types.Part.from_bytes(data=image, mime_type="image/png") for image in prompt.images]
                          if isinstance(prompt, ImageContextPrompt) else prompt),
                config=config,
            )

            parsed = None
            if schema is not None:
                if hasattr(response, "parsed") and isinstance(response.parsed, schema):
                    parsed = response.parsed
                elif response.text:
                    try:
                        parsed = schema.model_validate_json(response.text)
                    except ValueError:
                        pass  # Successful transport, invalid reasoning output; router handles quality escalation.

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
