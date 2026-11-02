import pytest

from agent.config import RouterSettings
from agent.providers import BaseProvider, ModelResult, QuotaExceededProviderError, RetryableProviderError, classify_error
from agent.router import ModelRouter


def test_explicit_cloud_quota_is_distinct_from_transient_capacity():
    class Error(Exception):
        status_code = 429
    error = classify_error(Error("Quota exceeded for global_online_prediction_requests_per_base_model"), "anthropic", "sonnet")
    assert isinstance(error, QuotaExceededProviderError)
    temporary = classify_error(Error("Resource exhausted: temporary capacity"), "google", "flash")
    assert isinstance(temporary, RetryableProviderError)
    assert not isinstance(temporary, QuotaExceededProviderError)


def test_quota_skips_retries_and_uses_configured_fallback():
    settings = RouterSettings(max_provider_retries=2)
    calls, sleeps, errors = [], [], []

    class Provider(BaseProvider):
        def generate(self, model_id, **kwargs):
            calls.append(model_id)
            if model_id == settings.primary.model_id:
                raise QuotaExceededProviderError("quota exhausted", "anthropic", model_id, status_code=429)
            return ModelResult("google", model_id, raw_text="OK")

    router = ModelRouter(settings, provider_factory=lambda name: Provider(), sleeper=sleeps.append, on_error=errors.append)
    result = router.route_normal("test")
    assert calls == [settings.primary.model_id, settings.fallback_1.model_id]
    assert sleeps == []
    assert result.provider_error_details[0]["error_type"] == "quota_exceeded"
    assert result.failover_occurred
    assert errors[0]["error_type"] == "quota_exceeded"
    assert errors[0]["model_id"] == settings.primary.model_id
