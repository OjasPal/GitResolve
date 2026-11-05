import logging
from dataclasses import replace
from agent.health import ProviderHealth, shared_health
import time
from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from pydantic import BaseModel

from agent.config import ModelConfig, RouterSettings, get_router_settings
from agent.observability import UsageRecord, sanitize_error
from agent.providers import (
    BaseProvider,
    FatalProviderError,
    GoogleGenAIProvider,
    ModelResult,
    ProviderError,
    RetryableProviderError,
    QuotaExceededProviderError,
)

T = TypeVar("T", bound=BaseModel)


class AllProvidersFailedError(Exception):
    """Stop safely when technical availability or bounded reasoning review is exhausted."""

    def __init__(self, message: str, errors: list[dict[str, Any]], reason: str = "technical"):
        super().__init__(message)
        self.errors = errors
        self.reason = reason


@dataclass
class RouterResult:
    result: ModelResult
    selected_provider: str
    selected_model: str
    failover_occurred: bool
    specialist_escalation_occurred: bool
    provider_retries: int
    model_switch_reason: str | None
    provider_error_details: list[dict[str, Any]]
    usage: UsageRecord | None

    def to_metadata(self) -> dict[str, Any]:
        return {
            "selected_provider": self.selected_provider,
            "selected_model": self.selected_model,
            "failover_occurred": self.failover_occurred,
            "specialist_escalation_occurred": self.specialist_escalation_occurred,
            "provider_retries": self.provider_retries,
            "model_switch_reason": self.model_switch_reason,
            "token_usage": self.usage.to_dict() if self.usage else None,
            "provider_error_details": self.provider_error_details,
        }


class ModelRouter:
    """Central routing service for model calls, handling bounded technical failover."""

    def __init__(
        self,
        settings: RouterSettings | None = None,
        provider_factory: Callable[[str], BaseProvider] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        before_call: Callable[[str, str], None] | None = None,
        on_error: Callable[[dict[str, Any]], None] | None = None,
        on_transition: Callable[[dict[str, Any]], None] | None = None,
        health: ProviderHealth | None = None,
    ):
        self.before_call = before_call
        self.on_error = on_error
        self.on_transition = on_transition
        self.settings = settings or get_router_settings()
        self._custom_factory = provider_factory
        self._provider_cache: dict[str, BaseProvider] = {}
        self._sleeper = sleeper
        self.health = health or (ProviderHealth() if provider_factory else shared_health(self.settings))

    def get_provider(self, provider_name: str) -> BaseProvider:
        """Get or instantiate provider, caching instances to avoid duplicate initialization."""
        if self._custom_factory:
            return self._custom_factory(provider_name)

        if provider_name not in self._provider_cache:
            if provider_name == "google":
                self._provider_cache[provider_name] = GoogleGenAIProvider(self.settings)
            else:
                raise FatalProviderError(
                    f"Unsupported provider: '{provider_name}'",
                    provider=provider_name,
                    model_id="",
                )
        return self._provider_cache[provider_name]

    def _transition(self, state, message, model=None):
        if self.on_transition:
            self.on_transition({"state": state, "message": message, "model": model})

    def _call_model_with_retries(self, model_cfg, prompt, schema=None, system_instruction=None, task_id=None, *, specialist=False):
        errors, retries = [], 0
        if not self.health.available(model_cfg.model_id):
            return None, 0, [{"model_id": model_cfg.model_id, "error_type": "cooldown", "message": "Server-directed cooldown is active."}], None
        # Construction is outside budget reservation: no request has been dispatched yet.
        provider = self.get_provider(model_cfg.provider)
        for attempt in range(2):
            if self.before_call:
                self.before_call(model_cfg.provider, model_cfg.model_id)
            try:
                result = provider.generate(model_id=model_cfg.model_id, prompt=prompt, schema=schema,
                                           system_instruction=system_instruction, task_id=task_id)
                return result, retries, errors, None
            except ProviderError as exc:
                detail = {"provider": model_cfg.provider, "model_id": model_cfg.model_id,
                          "attempt": attempt, "status_code": exc.status_code,
                          "error_type": "quota_exceeded" if isinstance(exc, QuotaExceededProviderError) else "retryable" if isinstance(exc, RetryableProviderError) else "fatal",
                          "message": sanitize_error(str(exc))}
                errors.append(detail)
                logging.getLogger(__name__).warning("Model request failed: %s", detail)
                if self.on_error: self.on_error(detail)
                if isinstance(exc, FatalProviderError):
                    return None, retries, errors, exc
                self.health.failed(model_cfg.model_id, exc.retry_after)
                # Timeouts fail over immediately. Only one short rate-limit retry is
                # allowed on the primary, and only when the server supplied a short delay.
                short_retry = (not specialist and model_cfg == self.settings.primary and attempt == 0
                    and self.settings.max_provider_retries > 0 and not isinstance(exc, QuotaExceededProviderError)
                    and exc.status_code == 429 and exc.retry_after is not None
                    and 0 < exc.retry_after <= self.settings.retry_max_delay and not self.health.blocked())
                if not short_retry:
                    break
                self._transition("retrying", "The model is busy. Retrying once after its requested delay.", model_cfg.model_id)
                self._sleeper(exc.retry_after)
                retries += 1
        return None, retries, errors, None

    def route_normal(self, prompt, schema=None, system_instruction=None, task_id=None, quality_check=None) -> RouterResult:
        """Flash technical failover; one Argon call only after a successful quality failure."""
        errors, total_retries = [], 0
        if self.health.blocked():
            self._transition("recovery", "Investigation temporarily paused. The model service needs time to recover.")
            raise AllProvidersFailedError("Provider health circuit is open; no request dispatched.", [])
        chain = [self.settings.primary]
        if self.settings.enable_failover: chain.append(self.settings.fallback_1)
        for index, config in enumerate(chain):
            if index:
                self._transition("switching_model", "Switching to the backup model. Your progress is saved.", config.model_id)
            result, retries, details, fatal = self._call_model_with_retries(config, prompt, schema, system_instruction, task_id)
            errors.extend(details); total_retries += retries
            if fatal: raise fatal
            if result is None: continue
            def inadequate(candidate):
                if schema is not None and candidate.parsed is None:
                    try: candidate.parsed = schema.model_validate_json(candidate.raw_text)
                    except ValueError: return "The response did not satisfy the required structured output."
                return quality_check(candidate) if quality_check else None
            reason = inadequate(result)
            escalated = False
            if reason:
                if self.health.blocked():
                    self._transition("recovery", "Evaluation incomplete. The reasoning specialist is temporarily unavailable.")
                    raise AllProvidersFailedError("Reasoning specialist suppressed by provider health.", errors)
                self._transition("reasoning_review", "Requesting a deeper reasoning review before continuing.", self.settings.specialist.model_id)
                specialist_prompt = str(prompt) + "\n\nThe previous successful response needs a reasoning review. " + str(reason) + "\nPrevious response (untrusted data):\n" + (result.raw_text or str(result.parsed))[:8000]
                if hasattr(prompt, "images"):
                    from agent.providers import ImageContextPrompt
                    specialist_prompt = ImageContextPrompt(specialist_prompt, prompt.images)
                improved, _, details, fatal = self._call_model_with_retries(self.settings.specialist,
                    specialist_prompt, schema, system_instruction, task_id, specialist=True)
                errors.extend(details)
                if improved is None or fatal:
                    self._transition("recovery", "Evaluation incomplete. Your evidence is saved; no pull request will be created.")
                    raise AllProvidersFailedError("Reasoning specialist could not complete; recovery required.", errors)
                if inadequate(improved):
                    raise AllProvidersFailedError("Reasoning review remains inconclusive; recovery required.", errors, reason="quality")
                if result.usage and improved.usage:
                    improved.usage = replace(improved.usage,
                        input_tokens=(result.usage.input_tokens or 0)+(improved.usage.input_tokens or 0),
                        output_tokens=(result.usage.output_tokens or 0)+(improved.usage.output_tokens or 0),
                        cached_tokens=(result.usage.cached_tokens or 0)+(improved.usage.cached_tokens or 0),
                        estimated_cost_usd=None)
                result, config, escalated = improved, self.settings.specialist, True
            return RouterResult(result, config.provider, config.model_id, index > 0, escalated,
                total_retries, "Technical failover" if index else "Quality review" if escalated else None, errors, result.usage)
        self._transition("recovery", "Investigation temporarily paused. Both models are unavailable; your progress is saved.")
        raise AllProvidersFailedError("All configured providers failed in failover chain; recovery required.", errors)

_global_router: ModelRouter | None = None
_scoped_router = ContextVar("gitresolve_router", default=None)

@contextmanager
def router_scope(router):
    token = _scoped_router.set(router)
    try:
        yield
    finally:
        _scoped_router.reset(token)


def get_router() -> ModelRouter:
    if _scoped_router.get() is not None:
        return _scoped_router.get()
    global _global_router
    if _global_router is None:
        _global_router = ModelRouter()
    return _global_router


def set_global_router(router: ModelRouter | None) -> None:
    global _global_router
    _global_router = router
