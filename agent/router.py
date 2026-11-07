import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any, Callable, TypeVar

from pydantic import BaseModel

from agent.config import ModelConfig, RouterSettings, get_router_settings
from agent.health import ProviderHealth, shared_health
from agent.observability import UsageRecord, sanitize_error
from agent.providers import (
    BaseProvider,
    FatalProviderError,
    GoogleGenAIProvider,
    ImageContextPrompt,
    ModelResult,
    ProviderError,
    QuotaExceededProviderError,
    RetryableProviderError,
    UnknownProviderError,
    invoke_provider_generate,
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
    lane: str = "full"
    node: str = ""
    latency_ms: float = 0.0
    reasoning_profile: str = "LOW"
    failure_class: str | None = None

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
            "lane": self.lane,
            "node": self.node,
            "latency_ms": self.latency_ms,
            "reasoning_profile": self.reasoning_profile,
            "failure_class": self.failure_class,
        }


class ModelRouter:
    """Central routing service for Google GenAI model calls, handling per-model breakers, lane rules, and Argon escalation."""

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
        self.telemetry_log: list[dict[str, Any]] = []

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
                    error_category="invalid_config",
                )
        return self._provider_cache[provider_name]

    def _transition(self, state: str, message: str, model: str | None = None) -> None:
        if self.on_transition:
            self.on_transition({"state": state, "message": message, "model": model})

    def _record_call_telemetry(
        self,
        *,
        model_id: str,
        lane: str,
        node: str,
        latency_ms: float,
        usage: UsageRecord | None,
        failure_class: str | None,
        reasoning_profile: str,
    ) -> None:
        entry = {
            "model": model_id,
            "lane": lane,
            "node": node,
            "latencyMs": round(latency_ms, 3),
            "inputTokens": getattr(usage, "input_tokens", None) if usage else None,
            "outputTokens": getattr(usage, "output_tokens", None) if usage else None,
            "failureClass": failure_class,
            "reasoningProfile": reasoning_profile,
        }
        self.telemetry_log = (self.telemetry_log + [entry])[-200:]

    def _call_model_with_retries(
        self,
        model_cfg: ModelConfig,
        prompt: Any,
        schema: Any = None,
        system_instruction: str | None = None,
        task_id: str | None = None,
        *,
        specialist: bool = False,
        lane: str = "full",
        node: str = "",
        thinking_level: str | None = None,
        timeout_seconds: float | None = None,
    ) -> tuple[ModelResult | None, int, list[dict[str, Any]], FatalProviderError | None, float]:
        errors: list[dict[str, Any]] = []
        retries = 0
        effective_profile = (thinking_level or self.settings.thinking_level or "LOW").upper()
        if not self.health.available(model_cfg.model_id):
            return (
                None,
                0,
                [
                    {
                        "model_id": model_cfg.model_id,
                        "error_type": "cooldown",
                        "error_category": "cooldown",
                        "lane": lane,
                        "node": node,
                        "message": "Per-model circuit breaker or server-directed cooldown is active.",
                    }
                ],
                None,
                0.0,
            )

        provider = self.get_provider(model_cfg.provider)
        total_latency_ms = 0.0
        for attempt in range(2):
            if self.before_call:
                self.before_call(model_cfg.provider, model_cfg.model_id)
            call_t0 = time.perf_counter()
            try:
                from githubapp.timing import measure

                with measure("model.generate", provider=model_cfg.provider, model=model_cfg.model_id) as timing:
                    result = invoke_provider_generate(
                        provider,
                        model_id=model_cfg.model_id,
                        prompt=prompt,
                        schema=schema,
                        system_instruction=system_instruction,
                        task_id=task_id,
                        thinking_level=thinking_level,
                        timeout_seconds=timeout_seconds,
                    )
                    if result.usage:
                        timing.update(
                            input_tokens=result.usage.input_tokens,
                            output_tokens=result.usage.output_tokens,
                        )
                elapsed_ms = (time.perf_counter() - call_t0) * 1000.0
                total_latency_ms += elapsed_ms
                self.health.record_success(model_cfg.model_id)
                self._record_call_telemetry(
                    model_id=model_cfg.model_id,
                    lane=lane,
                    node=node,
                    latency_ms=elapsed_ms,
                    usage=result.usage,
                    failure_class=None,
                    reasoning_profile=effective_profile,
                )
                return result, retries, errors, None, total_latency_ms
            except ProviderError as exc:
                elapsed_ms = (time.perf_counter() - call_t0) * 1000.0
                total_latency_ms += elapsed_ms
                err_type = (
                    "quota_exceeded"
                    if isinstance(exc, QuotaExceededProviderError)
                    else "unknown"
                    if isinstance(exc, UnknownProviderError)
                    else "retryable"
                    if isinstance(exc, RetryableProviderError)
                    else "fatal"
                )
                err_cat = getattr(exc, "error_category", None) or err_type
                detail = {
                    "provider": model_cfg.provider,
                    "model_id": model_cfg.model_id,
                    "attempt": attempt,
                    "status_code": exc.status_code,
                    "error_type": err_type,
                    "error_category": err_cat,
                    "lane": lane,
                    "node": node,
                    "latency_ms": round(elapsed_ms, 3),
                    "message": sanitize_error(str(exc)),
                }
                errors.append(detail)
                self._record_call_telemetry(
                    model_id=model_cfg.model_id,
                    lane=lane,
                    node=node,
                    latency_ms=elapsed_ms,
                    usage=None,
                    failure_class=err_cat,
                    reasoning_profile=effective_profile,
                )
                logging.getLogger(__name__).warning("Model request failed: %s", detail)
                if self.on_error:
                    self.on_error(detail)
                if isinstance(exc, FatalProviderError):
                    return None, retries, errors, exc, total_latency_ms
                self.health.failed(model_cfg.model_id, exc.retry_after)
                # Timeouts fail over immediately. Only one short rate-limit retry is
                # allowed on the primary, and only when the server supplied a short delay.
                short_retry = (
                    not specialist
                    and model_cfg == self.settings.primary
                    and attempt == 0
                    and self.settings.max_provider_retries > 0
                    and not isinstance(exc, QuotaExceededProviderError)
                    and exc.status_code == 429
                    and exc.retry_after is not None
                    and 0 < exc.retry_after <= self.settings.retry_max_delay
                    and not self.health.blocked()
                )
                if not short_retry:
                    break
                self._transition(
                    "retrying",
                    "The model is busy. Retrying once after its requested delay.",
                    model_cfg.model_id,
                )
                self._sleeper(exc.retry_after)
                retries += 1
        return None, retries, errors, None, total_latency_ms

    def route_specialist(
        self,
        prompt: Any,
        schema: Any = None,
        system_instruction: str | None = None,
        task_id: str | None = None,
        *,
        lane: str = "full",
        node: str = "specialist_reflect",
        thinking_level: str = "HIGH",
        timeout_seconds: float | None = None,
    ) -> RouterResult:
        """Invoke gemini-4-argon directly for full-lane reflection/diagnosis.

        Ground rules:
        - NEVER allowed in the fast lane.
        - Independent per-model circuit breaker: an open primary/fallback breaker does NOT block the specialist.
        """
        if lane == "fast":
            raise ValueError("Adaptive Lanes violation: fast lane is never permitted to invoke the specialist model.")
        spec_cfg = self.settings.specialist
        if not self.health.available(spec_cfg.model_id):
            self._transition("recovery", "The reasoning specialist circuit breaker is open.")
            raise AllProvidersFailedError(
                "Specialist circuit breaker is open; cannot dispatch specialist request.",
                [{"model_id": spec_cfg.model_id, "error_type": "cooldown", "error_category": "cooldown"}],
            )
        self._transition(
            "reasoning_review",
            "Escalating to the Argon specialist for structured trajectory reflection.",
            spec_cfg.model_id,
        )
        result, retries, details, fatal, latency_ms = self._call_model_with_retries(
            spec_cfg,
            prompt,
            schema,
            system_instruction,
            task_id,
            specialist=True,
            lane=lane,
            node=node,
            thinking_level=thinking_level,
            timeout_seconds=timeout_seconds,
        )
        if fatal is not None:
            raise fatal
        if result is None:
            self._transition("recovery", "The reasoning specialist could not complete the request.")
            raise AllProvidersFailedError("Reasoning specialist request failed.", details)
        if schema is not None and result.parsed is None:
            try:
                result.parsed = schema.model_validate_json(result.raw_text)
            except ValueError as exc:
                raise AllProvidersFailedError(
                    "Reasoning specialist returned malformed structured output.",
                    details,
                    reason="quality",
                ) from exc
        return RouterResult(
            result=result,
            selected_provider=spec_cfg.provider,
            selected_model=spec_cfg.model_id,
            failover_occurred=False,
            specialist_escalation_occurred=True,
            provider_retries=retries,
            model_switch_reason="Specialist reflection",
            provider_error_details=details,
            usage=result.usage,
            lane=lane,
            node=node,
            latency_ms=round(latency_ms, 3),
            reasoning_profile=thinking_level,
            failure_class=details[-1].get("error_category") if details else None,
        )

    def route_normal(
        self,
        prompt: Any,
        schema: Any = None,
        system_instruction: str | None = None,
        task_id: str | None = None,
        quality_check: Callable[[ModelResult], str | None] | None = None,
        *,
        lane: str = "full",
        node: str = "",
        allow_specialist: bool | None = None,
        thinking_level: str | None = None,
        timeout_seconds: float | None = None,
    ) -> RouterResult:
        """Primary Flash -> Fallback Flash technical failover; optional Argon quality escalation in full lane only."""
        errors: list[dict[str, Any]] = []
        total_retries = 0
        total_latency_ms = 0.0
        effective_profile = (
            "LOW" if lane == "fast" else (thinking_level or self.settings.thinking_level or "LOW").upper()
        )
        effective_timeout = timeout_seconds
        if lane == "fast" and effective_timeout is None:
            effective_timeout = self.settings.fast_request_timeout_seconds

        # Ground rule: the fast lane NEVER calls the specialist.
        can_use_specialist = False if lane == "fast" else (True if allow_specialist is None else bool(allow_specialist))

        if self.health.blocked():
            self._transition("recovery", "Investigation temporarily paused. The model service needs time to recover.")
            raise AllProvidersFailedError("Provider health circuit is open; no request dispatched.", [])

        chain = [self.settings.primary]
        if self.settings.enable_failover:
            chain.append(self.settings.fallback_1)

        for index, config in enumerate(chain):
            if index:
                self._transition(
                    "switching_model",
                    "Switching to the backup model. Your progress is saved.",
                    config.model_id,
                )
            result, retries, details, fatal, call_ms = self._call_model_with_retries(
                config,
                prompt,
                schema,
                system_instruction,
                task_id,
                lane=lane,
                node=node,
                thinking_level=effective_profile,
                timeout_seconds=effective_timeout,
            )
            errors.extend(details)
            total_retries += retries
            total_latency_ms += call_ms
            if fatal:
                raise fatal
            if result is None:
                continue

            def inadequate(candidate: ModelResult) -> str | None:
                if schema is not None and candidate.parsed is None:
                    try:
                        candidate.parsed = schema.model_validate_json(candidate.raw_text)
                    except ValueError as exc:
                        return f"The response did not satisfy the required structured output: {str(exc)[:300]}"
                return quality_check(candidate) if quality_check else None

            reason = inadequate(result)
            escalated = False
            if reason:
                if not can_use_specialist:
                    raise AllProvidersFailedError(
                        f"Fast-lane or non-specialist response failed quality check: {reason}",
                        errors,
                        reason="quality",
                    )
                if (
                    self.health.blocked()
                    or not self.health.available(self.settings.specialist.model_id)
                    or (not self.health.available(self.settings.primary.model_id) and allow_specialist is not True)
                ):
                    self._transition(
                        "recovery",
                        "Evaluation incomplete. The reasoning specialist is temporarily unavailable.",
                    )
                    raise AllProvidersFailedError("Reasoning specialist suppressed by provider health.", errors)
                self._transition(
                    "reasoning_review",
                    "Requesting a deeper reasoning review before continuing.",
                    self.settings.specialist.model_id,
                )
                specialist_prompt: Any = (
                    str(prompt)
                    + "\n\nThe previous successful response needs a reasoning review. "
                    + str(reason)
                    + "\nPrevious response (untrusted data):\n"
                    + (result.raw_text or str(result.parsed))[:8000]
                )
                if hasattr(prompt, "images"):
                    specialist_prompt = ImageContextPrompt(specialist_prompt, prompt.images)
                improved, _, details, fatal, spec_ms = self._call_model_with_retries(
                    self.settings.specialist,
                    specialist_prompt,
                    schema,
                    system_instruction,
                    task_id,
                    specialist=True,
                    lane=lane,
                    node=node or "quality_specialist",
                    thinking_level="HIGH",
                    timeout_seconds=self.settings.specialist_timeout_seconds,
                )
                errors.extend(details)
                total_latency_ms += spec_ms
                if improved is None or fatal:
                    self._transition(
                        "recovery",
                        "Evaluation incomplete. Your evidence is saved; no pull request will be created.",
                    )
                    raise AllProvidersFailedError("Reasoning specialist could not complete; recovery required.", errors)
                if inadequate(improved):
                    raise AllProvidersFailedError(
                        "Reasoning review remains inconclusive; recovery required.",
                        errors,
                        reason="quality",
                    )
                if result.usage and improved.usage:
                    improved.usage = replace(
                        improved.usage,
                        input_tokens=(result.usage.input_tokens or 0) + (improved.usage.input_tokens or 0),
                        output_tokens=(result.usage.output_tokens or 0) + (improved.usage.output_tokens or 0),
                        cached_tokens=(result.usage.cached_tokens or 0) + (improved.usage.cached_tokens or 0),
                        estimated_cost_usd=None,
                    )
                result, config, escalated = improved, self.settings.specialist, True
            return RouterResult(
                result=result,
                selected_provider=config.provider,
                selected_model=config.model_id,
                failover_occurred=index > 0,
                specialist_escalation_occurred=escalated,
                provider_retries=total_retries,
                model_switch_reason="Technical failover" if index else "Quality review" if escalated else None,
                provider_error_details=errors,
                usage=result.usage,
                lane=lane,
                node=node,
                latency_ms=round(total_latency_ms, 3),
                reasoning_profile="HIGH" if escalated else effective_profile,
                failure_class=errors[-1].get("error_category") if errors else None,
            )

        self._transition(
            "recovery",
            "Investigation temporarily paused. Both models are unavailable; your progress is saved.",
        )
        raise AllProvidersFailedError("All configured providers failed in failover chain; recovery required.", errors)


_global_router: ModelRouter | None = None
_scoped_router: ContextVar[ModelRouter | None] = ContextVar("gitresolve_router", default=None)


@contextmanager
def router_scope(router: ModelRouter):
    token = _scoped_router.set(router)
    try:
        yield
    finally:
        _scoped_router.reset(token)


def get_router() -> ModelRouter:
    if _scoped_router.get() is not None:
        return _scoped_router.get()  # type: ignore[return-value]
    global _global_router
    if _global_router is None:
        _global_router = ModelRouter()
    return _global_router


def set_global_router(router: ModelRouter | None) -> None:
    global _global_router
    _global_router = router
