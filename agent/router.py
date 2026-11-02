import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from pydantic import BaseModel

from agent.config import ModelConfig, RouterSettings, get_router_settings
from agent.observability import UsageRecord, sanitize_error
from agent.providers import (
    AnthropicVertexProvider,
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
    """Raised when all configured providers in the failover chain fail with technical errors."""

    def __init__(self, message: str, errors: list[dict[str, Any]]):
        super().__init__(message)
        self.errors = errors


class EscalationLimitExceededError(Exception):
    """Raised when specialist escalation attempts exceed the configured maximum."""
    pass


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
    """Central routing service for model calls, handling technical failover and specialist escalation."""

    def __init__(
        self,
        settings: RouterSettings | None = None,
        provider_factory: Callable[[str], BaseProvider] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        before_call: Callable[[str, str], None] | None = None,
        on_error: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.before_call = before_call
        self.on_error = on_error
        self.settings = settings or get_router_settings()
        self._custom_factory = provider_factory
        self._provider_cache: dict[str, BaseProvider] = {}
        self._sleeper = sleeper

    def get_provider(self, provider_name: str) -> BaseProvider:
        """Get or instantiate provider, caching instances to avoid duplicate initialization."""
        if self._custom_factory:
            return self._custom_factory(provider_name)

        if provider_name not in self._provider_cache:
            if provider_name == "anthropic":
                self._provider_cache[provider_name] = AnthropicVertexProvider(self.settings)
            elif provider_name == "google":
                self._provider_cache[provider_name] = GoogleGenAIProvider(self.settings)
            else:
                raise FatalProviderError(
                    f"Unsupported provider: '{provider_name}'",
                    provider=provider_name,
                    model_id="",
                )
        return self._provider_cache[provider_name]

    def _sleep_backoff(self, attempt: int) -> None:
        delay = min(
            self.settings.retry_max_delay,
            self.settings.retry_initial_delay * (self.settings.retry_backoff_factor ** attempt),
        )
        jitter = random.uniform(0.8, 1.2)
        self._sleeper(delay * jitter)

    def _call_model_with_retries(
        self,
        model_cfg: ModelConfig,
        prompt: str,
        schema: type[T] | None = None,
        system_instruction: str | None = None,
        task_id: str | None = None,
    ) -> tuple[ModelResult | None, int, list[dict[str, Any]], FatalProviderError | None]:
        """Call a specific model with bounded retries for transient errors."""
        provider = self.get_provider(model_cfg.provider)
        retries = 0
        error_details: list[dict[str, Any]] = []

        max_attempts = self.settings.max_provider_retries + 1
        for attempt in range(max_attempts):
            if attempt > 0:
                retries += 1
            if self.before_call is not None:
                self.before_call(model_cfg.provider, model_cfg.model_id)
            try:
                res = provider.generate(
                    model_id=model_cfg.model_id,
                    prompt=prompt,
                    schema=schema,
                    system_instruction=system_instruction,
                    task_id=task_id,
                )
                return res, retries, error_details, None

            except FatalProviderError as fatal_err:
                # Fatal errors (401/403/400/config) fail immediately without retrying or falling back
                error_details.append({
                    "provider": model_cfg.provider,
                    "model_id": model_cfg.model_id,
                    "attempt": attempt,
                    "status_code": fatal_err.status_code,
                    "error_type": "fatal",
                    "message": sanitize_error(str(fatal_err)),
                })
                if self.on_error:
                    self.on_error(error_details[-1])
                return None, retries, error_details, fatal_err

            except QuotaExceededProviderError as quota_err:
                error_details.append({"provider": model_cfg.provider, "model_id": model_cfg.model_id,
                    "attempt": attempt, "status_code": 429, "error_type": "quota_exceeded",
                    "message": sanitize_error(str(quota_err))})
                if self.on_error:
                    self.on_error(error_details[-1])
                break

            except RetryableProviderError as retry_err:
                error_details.append({
                    "provider": model_cfg.provider,
                    "model_id": model_cfg.model_id,
                    "attempt": attempt,
                    "status_code": retry_err.status_code,
                    "error_type": "retryable",
                    "message": sanitize_error(str(retry_err)),
                })
                if self.on_error:
                    self.on_error(error_details[-1])
                if attempt < max_attempts - 1:
                    self._sleep_backoff(attempt)
                else:
                    # Retries exhausted for this provider
                    break

            except Exception as exc:
                # Any other unexpected exception
                error_details.append({
                    "provider": model_cfg.provider,
                    "model_id": model_cfg.model_id,
                    "attempt": attempt,
                    "error_type": "unexpected",
                    "message": sanitize_error(str(exc)),
                })
                if self.on_error:
                    self.on_error(error_details[-1])
                return None, retries, error_details, FatalProviderError(
                    f"Unexpected error: {exc}",
                    provider=model_cfg.provider,
                    model_id=model_cfg.model_id,
                    original_error=exc,
                )

        return None, retries, error_details, None

    def route_normal(
        self,
        prompt: str,
        schema: type[T] | None = None,
        system_instruction: str | None = None,
        task_id: str | None = None,
    ) -> RouterResult:
        """Execute normal agent call via Primary (Sonnet 5.5) with technical failover to Gemini 4 and Gemini Flash.
        
        DOES NOT fallback on reasoning errors or fatal configuration errors.
        """
        all_error_details: list[dict[str, Any]] = []
        total_retries = 0

        # Build failover chain: Primary -> Fallback 1 -> Fallback 2
        chain = [self.settings.primary]
        if self.settings.enable_failover:
            chain.append(self.settings.fallback_1)
            chain.append(self.settings.fallback_2)

        last_switch_reason: str | None = None
        failover_occurred = False

        for idx, model_cfg in enumerate(chain):
            if idx > 0:
                failover_occurred = True

            result, retries, errors, fatal_err = self._call_model_with_retries(
                model_cfg=model_cfg,
                prompt=prompt,
                schema=schema,
                system_instruction=system_instruction,
                task_id=task_id,
            )
            total_retries += retries
            all_error_details.extend(errors)

            if fatal_err is not None:
                # Fatal configuration/auth error: fail immediately, do not failover!
                raise fatal_err

            if result is not None:
                # Success!
                return RouterResult(
                    result=result,
                    selected_provider=model_cfg.provider,
                    selected_model=model_cfg.model_id,
                    failover_occurred=failover_occurred,
                    specialist_escalation_occurred=False,
                    provider_retries=total_retries,
                    model_switch_reason=last_switch_reason,
                    provider_error_details=all_error_details,
                    usage=result.usage,
                )

            # Record reason for switching to next model in failover chain
            last_err_msg = errors[-1]["message"] if errors else "exhausted retries"
            last_switch_reason = (
                f"Failover from {model_cfg.provider}/{model_cfg.model_id} "
                f"due to transient provider failure: {last_err_msg}"
            )

        # All models in technical failover chain failed
        raise AllProvidersFailedError(
            f"All configured providers failed in failover chain: {[c.model_id for c in chain]}",
            errors=all_error_details,
        )

    def escalate_specialist(
        self,
        prompt: str,
        escalation_reason: str,
        schema: type[T] | None = None,
        system_instruction: str | None = None,
        task_id: str | None = None,
        current_escalation_count: int = 0,
    ) -> RouterResult:
        """Explicitly invoke Claude Opus 5.5 for specialist reasoning escalation.
        
        This is NOT part of the normal failover chain and cannot be triggered by technical outages.
        """
        if not self.settings.enable_opus_escalation:
            raise RuntimeError("Specialist reasoning escalation is disabled by configuration (LLM_ENABLE_OPUS_ESCALATION=false).")

        if current_escalation_count >= self.settings.max_opus_escalations:
            raise EscalationLimitExceededError(
                f"Specialist escalation limit reached ({current_escalation_count}/{self.settings.max_opus_escalations}). "
                f"Cannot escalate further."
            )

        opus_cfg = self.settings.escalation
        result, retries, errors, fatal_err = self._call_model_with_retries(
            model_cfg=opus_cfg,
            prompt=prompt,
            schema=schema,
            system_instruction=system_instruction,
            task_id=task_id,
        )

        if fatal_err is not None:
            raise fatal_err

        if result is None:
            # Opus transient failure
            raise AllProvidersFailedError(
                f"Specialist model ({opus_cfg.model_id}) call failed after {retries} retries.",
                errors=errors,
            )

        return RouterResult(
            result=result,
            selected_provider=opus_cfg.provider,
            selected_model=opus_cfg.model_id,
            failover_occurred=False,
            specialist_escalation_occurred=True,
            provider_retries=retries,
            model_switch_reason=f"Specialist reasoning escalation: {escalation_reason}",
            provider_error_details=errors,
            usage=result.usage,
        )


_global_router: ModelRouter | None = None


def get_router() -> ModelRouter:
    global _global_router
    if _global_router is None:
        _global_router = ModelRouter()
    return _global_router


def set_global_router(router: ModelRouter | None) -> None:
    global _global_router
    _global_router = router
