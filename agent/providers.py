import abc
import inspect
import socket
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from types import SimpleNamespace
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
    """Base exception for provider calls with structured error_category classification."""

    def __init__(
        self,
        message: str,
        provider: str,
        model_id: str,
        status_code: int | None = None,
        original_error: Exception | None = None,
        retry_after: float | None = None,
        error_category: str | None = None,
    ):
        super().__init__(sanitize_error(message))
        self.provider = provider
        self.model_id = model_id
        self.status_code = status_code
        self.original_error = original_error
        self.retry_after = retry_after
        self.error_category = error_category or self._infer_category(message, status_code)

    @staticmethod
    def _infer_category(message: str, status_code: int | None) -> str:
        msg = (message or "").lower()
        if status_code in (401, 403) or "unauthorized" in msg or "forbidden" in msg or "credentials" in msg:
            return "auth"
        if status_code in (400, 404) or "invalid model" in msg or "unsupported provider" in msg or "must be configured" in msg:
            return "invalid_config"
        if status_code in (408, 504) or "timeout" in msg or "timed out" in msg or "deadline" in msg:
            return "timeout"
        if status_code == 429 or "rate limit" in msg or "quota" in msg or "resource exhausted" in msg:
            return "quota_exceeded" if "quota" in msg else "rate_limit"
        if status_code in (500, 502, 503, 529) or "bad gateway" in msg or "service unavailable" in msg:
            return "server_5xx"
        return "unknown"


class RetryableProviderError(ProviderError):
    """Transient/retryable provider error (e.g. 429, 5xx, network timeout)."""
    pass


class QuotaExceededProviderError(RetryableProviderError):
    """Explicit quota exhaustion: switch model without hammering the same quota."""

    def __init__(self, message: str, provider: str, model_id: str, status_code: int | None = 429, original_error: Exception | None = None, retry_after: float | None = None):
        super().__init__(
            message,
            provider,
            model_id,
            status_code=status_code,
            original_error=original_error,
            retry_after=retry_after,
            error_category="quota_exceeded",
        )


class UnknownProviderError(RetryableProviderError):
    """Unclassified provider exception: gets one fallback/retry instead of instant fatal failure."""

    def __init__(self, message: str, provider: str, model_id: str, status_code: int | None = None, original_error: Exception | None = None, retry_after: float | None = None):
        super().__init__(
            message,
            provider,
            model_id,
            status_code=status_code,
            original_error=original_error,
            retry_after=retry_after,
            error_category="unknown",
        )


class FatalProviderError(ProviderError):
    """Non-retryable permanent error (auth 401/403, invalid model config 400/404)."""
    pass


def _classify_error(exc: Exception, provider: str, model_id: str) -> ProviderError:
    """Classify an exception into structured categories: timeout, 429/quota, 5xx, invalid_config, auth, unknown."""
    status_code: int | None = None

    if hasattr(exc, "status_code") and isinstance(getattr(exc, "status_code"), int):
        status_code = exc.status_code
    elif hasattr(exc, "code") and isinstance(getattr(exc, "code"), int):
        status_code = exc.code

    msg = str(exc).lower()

    # 1. Explicit quota exhaustion vs transient 429 rate limit
    if status_code == 429 and any(term in msg for term in ("quota exceeded", "quota_exceeded", "quota increase")):
        return QuotaExceededProviderError(
            "Cloud model quota exhausted. Check Vertex AI quotas for this model/project or use an available configured model.",
            provider,
            model_id,
            status_code=429,
            original_error=exc,
        )

    # 2. Permanent auth errors (never retried)
    if "DefaultCredentialsError" in type(exc).__name__ or status_code in (401, 403) or any(
        term in msg for term in ("permission_denied", "unauthenticated", "invalid api key", "invalid credentials")
    ):
        return FatalProviderError(
            f"Authentication/authorization error ({status_code or 401}): {exc}",
            provider,
            model_id,
            status_code=status_code or 401,
            original_error=exc,
            error_category="auth",
        )

    # 3. Permanent invalid model / request config errors (never retried)
    if status_code in (400, 404) or (
        genai_errors is not None and isinstance(exc, genai_errors.ClientError) and status_code in (400, 401, 403, 404)
    ) or any(term in msg for term in ("invalid_argument", "model not found", "not found: models/")):
        return FatalProviderError(
            f"Invalid model or request configuration ({status_code or 400}): {exc}",
            provider,
            model_id,
            status_code=status_code or 400,
            original_error=exc,
            error_category="invalid_config",
        )

    # 4. Timeouts
    if status_code in (408, 504) or isinstance(exc, (TimeoutError, socket.timeout)) or any(
        k in msg for k in ("timed out", "timeout", "deadline exceeded", "deadline_exceeded")
    ):
        return RetryableProviderError(
            f"Model request timed out ({status_code or 504}): {exc}",
            provider,
            model_id,
            status_code=status_code or 504,
            original_error=exc,
            error_category="timeout",
        )

    # 5. Transient 429 rate limit
    if status_code == 429 or any(k in msg for k in ("rate limit", "resource exhausted", "too many requests")):
        return RetryableProviderError(
            f"Transient rate limit ({status_code or 429}): {exc}",
            provider,
            model_id,
            status_code=status_code or 429,
            original_error=exc,
            error_category="rate_limit",
        )

    # 6. Transient 5xx / network interruption
    retryable_types: list[type[Exception]] = [ConnectionError]
    if genai_errors is not None:
        retryable_types.append(genai_errors.ServerError)
    if (
        status_code in (500, 502, 503, 529)
        or isinstance(exc, tuple(retryable_types))
        or any(k in msg for k in ("connection reset", "connection refused", "econnreset", "socket hang up", "502", "503"))
    ):
        return RetryableProviderError(
            f"Transient server/network error ({status_code or 503}): {exc}",
            provider,
            model_id,
            status_code=status_code,
            original_error=exc,
            error_category="server_5xx",
        )

    # 7. Unknown errors: classify as UnknownProviderError (retryable once / eligible for fallback, not instant fatal)
    return UnknownProviderError(
        f"Unclassified provider error: {exc}",
        provider,
        model_id,
        status_code=status_code,
        original_error=exc,
    )


def classify_error(exc: Exception, provider: str, model_id: str) -> ProviderError:
    classified = _classify_error(exc, provider, model_id)
    headers = getattr(getattr(exc, "response", None), "headers", None) or getattr(exc, "headers", {}) or {}
    hint = headers.get("Retry-After") or headers.get("retry-after")
    try:
        classified.retry_after = max(0.0, float(hint))
    except (TypeError, ValueError):
        try:
            classified.retry_after = max(0.0, (parsedate_to_datetime(hint) - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            details = (getattr(exc, "response_json", {}) or {}).get("error", {}).get("details", [])
            for detail in details:
                if "RetryInfo" in detail.get("@type", ""):
                    try:
                        classified.retry_after = max(0.0, float(detail["retryDelay"].rstrip("s")))
                    except (KeyError, TypeError, ValueError):
                        pass
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


def invoke_provider_generate(
    provider: BaseProvider,
    *,
    model_id: str,
    prompt: Any,
    schema: Any = None,
    system_instruction: str | None = None,
    task_id: str | None = None,
    thinking_level: str | None = None,
    timeout_seconds: float | None = None,
) -> ModelResult:
    """Invoke provider.generate, passing per-node thinking_level and timeout_seconds when explicitly declared."""
    sig = inspect.signature(provider.generate)
    base_kwargs: dict[str, Any] = {
        "model_id": model_id,
        "prompt": prompt,
        "schema": schema,
        "system_instruction": system_instruction,
        "task_id": task_id,
    }
    ext_kwargs = dict(base_kwargs)
    if thinking_level is not None and "thinking_level" in sig.parameters:
        ext_kwargs["thinking_level"] = thinking_level
    if timeout_seconds is not None and "timeout_seconds" in sig.parameters:
        ext_kwargs["timeout_seconds"] = timeout_seconds
    return provider.generate(**ext_kwargs)


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
                    error_category="invalid_config",
                )
            self._client = genai.Client(
                enterprise=self.enterprise,
                project=self.project,
                location=self.location,
                http_options=types.HttpOptions(
                    timeout=int(self.settings.request_timeout_seconds * 1000),
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
        return self._client

    def generate(
        self,
        model_id: str,
        prompt: str,
        schema: type[T] | None = None,
        system_instruction: str | None = None,
        task_id: str | None = None,
        *,
        thinking_level: str | None = None,
        timeout_seconds: float | None = None,
        stream: bool = False,
    ) -> ModelResult:
        try:
            client = self._get_client()

            default_timeout = (
                self.settings.specialist_timeout_seconds
                if model_id == self.settings.specialist.model_id
                else self.settings.request_timeout_seconds
            )
            effective_timeout = (
                max(5.0, min(default_timeout, float(timeout_seconds)))
                if timeout_seconds is not None
                else default_timeout
            )
            effective_thinking = (thinking_level or self.settings.thinking_level or "LOW").upper()
            sdk_thinking = (
                "LOW"
                if schema is not None and len(str(prompt)) > 12000
                else effective_thinking
            )

            config_kwargs: dict[str, Any] = {
                "max_output_tokens": self.settings.max_output_tokens,
                "http_options": types.HttpOptions(
                    timeout=int(effective_timeout * 1000),
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            }
            if model_id.startswith(("gemini-3", "gemini-4")):
                config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=sdk_thinking)
            if system_instruction:
                config_kwargs["system_instruction"] = system_instruction

            if schema is not None:
                config_kwargs["response_mime_type"] = "application/json"
                config_kwargs["response_schema"] = schema

            config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None
            contents_payload = (
                [types.Part.from_text(text=str(prompt))]
                + [types.Part.from_bytes(data=image, mime_type="image/png") for image in prompt.images]
                if isinstance(prompt, ImageContextPrompt)
                else prompt
            )

            from githubapp.timing import model_dispatched
            model_dispatched()
            if stream and schema is None and hasattr(client.models, "generate_content_stream"):
                chunks: list[str] = []
                last_chunk: Any = None
                for chunk in client.models.generate_content_stream(
                    model=model_id,
                    contents=contents_payload,
                    config=config,
                ):
                    last_chunk = chunk
                    if getattr(chunk, "text", None):
                        chunks.append(chunk.text)
                response = SimpleNamespace(
                    text="".join(chunks),
                    parsed=None,
                    usage_metadata=getattr(last_chunk, "usage_metadata", None) if last_chunk is not None else None,
                )
            else:
                response = client.models.generate_content(
                    model=model_id,
                    contents=contents_payload,
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
