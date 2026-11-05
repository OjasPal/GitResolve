from dataclasses import replace
from types import SimpleNamespace
import pytest
from pydantic import BaseModel
from agent.config import ModelConfig, RouterSettings
from agent.health import ProviderHealth
from agent.providers import BaseProvider, ModelResult, RetryableProviderError, classify_error
from agent.router import ModelRouter, AllProvidersFailedError
from agent.observability import create_usage_record

PRIMARY, BACKUP, SPECIALIST = "gemini-3.8-flash", "gemini-3.6-flash", "gemini-4-argon"


class Answer(BaseModel):
    answer: str


def response(model=PRIMARY, text="ready"):
    return ModelResult("google", model, parsed=Answer(answer=text), usage=create_usage_record("google", model, 10, 5))


def timeout(model):
    return RetryableProviderError("deadline exceeded", "google", model, 504)


def routing(sequence, health=None, before=None):
    calls, sleeps, events = [], [], []
    class Provider(BaseProvider):
        def generate(self, model_id, **kwargs):
            calls.append(model_id)
            item = sequence.pop(0)
            if isinstance(item, Exception): raise item
            return item
    settings = RouterSettings(primary=ModelConfig("google", PRIMARY), fallback_1=ModelConfig("google", BACKUP), specialist=ModelConfig("google", SPECIALIST))
    return ModelRouter(settings, provider_factory=lambda _:Provider(), sleeper=sleeps.append,
        health=health, before_call=before, on_transition=events.append), calls, sleeps, events


def test_primary_success_uses_one_request():
    router,calls,_,_=routing([response()])
    assert router.route_normal("request",schema=Answer).selected_model==PRIMARY
    assert calls==[PRIMARY]


def test_timeout_switches_immediately_without_specialist_or_sleep():
    router,calls,sleeps,events=routing([timeout(PRIMARY),response(BACKUP)])
    result=router.route_normal("request",schema=Answer)
    assert calls==[PRIMARY,BACKUP] and not sleeps
    assert result.failover_occurred and not result.specialist_escalation_occurred
    assert events[0]["state"]=="switching_model"


def test_both_technical_failures_enter_recovery_without_argon():
    router,calls,sleeps,events=routing([timeout(PRIMARY),timeout(BACKUP)])
    with pytest.raises(AllProvidersFailedError): router.route_normal("request",schema=Answer)
    assert calls==[PRIMARY,BACKUP] and not sleeps
    assert events[-1]["state"]=="recovery"
    with pytest.raises(AllProvidersFailedError): router.route_normal("request",schema=Answer)
    assert len(calls)==2  # circuit blocks a new call, not a hidden retry


def test_one_short_server_directed_retry_only():
    error=RetryableProviderError("busy","google",PRIMARY,429,retry_after=1)
    router,calls,sleeps,_=routing([error,response()])
    assert router.route_normal("request").provider_retries==1
    assert calls==[PRIMARY,PRIMARY] and sleeps==[1]


def test_long_retry_after_is_not_truncated_or_ignored_on_next_call():
    error=RetryableProviderError("busy","google",PRIMARY,429,retry_after=90)
    router,calls,sleeps,_=routing([error,response(BACKUP),response(BACKUP)])
    router.route_normal("request")
    router.route_normal("request")
    assert calls==[PRIMARY,BACKUP,BACKUP] and not sleeps


def test_quality_failure_uses_one_specialist_and_aggregates_usage():
    router,calls,_,_=routing([response(text="inconclusive"),response(SPECIALIST)])
    result=router.route_normal("request",schema=Answer,quality_check=lambda r:"Missing reasoning" if r.parsed.answer=="inconclusive" else None)
    assert calls==[PRIMARY,SPECIALIST] and result.specialist_escalation_occurred
    assert result.usage.input_tokens==20 and result.usage.output_tokens==10


def test_argon_timeout_enters_recovery_immediately():
    router,calls,sleeps,_=routing([response(text="inconclusive"),timeout(SPECIALIST)])
    with pytest.raises(AllProvidersFailedError):
        router.route_normal("request",schema=Answer,quality_check=lambda r:"Missing evidence")
    assert calls==[PRIMARY,SPECIALIST] and not sleeps


def test_recent_provider_failures_suppress_specialist():
    health=ProviderHealth();health.failed(PRIMARY)
    router,calls,_,_=routing([timeout(PRIMARY),response(BACKUP,text="inconclusive")],health=health)
    with pytest.raises(AllProvidersFailedError):
        router.route_normal("request",schema=Answer,quality_check=lambda r:"Missing evidence")
    assert calls==[PRIMARY,BACKUP]


def test_circuit_recovers_after_bounded_cooldown():
    clock=[100.0];health=ProviderHealth(clock=lambda:clock[0])
    health.failed(PRIMARY);health.failed(BACKUP)
    assert health.blocked()
    clock[0]+=61
    assert not health.blocked()


def test_budget_rejection_prevents_dispatch():
    count=[]
    def reserve(provider,model):
        if count: raise RuntimeError("budget exhausted")
        count.append(model)
    router,calls,_,_=routing([timeout(PRIMARY),response(BACKUP)],before=reserve)
    with pytest.raises(RuntimeError,match="budget exhausted"): router.route_normal("request")
    assert calls==[PRIMARY] and count==[PRIMARY]


def test_retry_after_headers_are_read_without_exposing_them():
    class Error(Exception):
        status_code=429
        response=SimpleNamespace(headers={"Retry-After":"12"})
    assert classify_error(Error("busy"),"google",PRIMARY).retry_after==12


def test_config_caps_legacy_long_timeouts_and_retries():
    settings=RouterSettings(request_timeout_seconds=180,max_provider_retries=8,specialist_timeout_seconds=300)
    assert settings.request_timeout_seconds==60 and settings.specialist_timeout_seconds==60
    assert settings.max_provider_retries==1


def test_specialist_inconclusive_is_never_accepted():
    router,calls,_,_=routing([response(),response(SPECIALIST)])
    with pytest.raises(AllProvidersFailedError):
        router.route_normal("request",schema=Answer,quality_check=lambda r:"Evidence still missing")
    assert calls==[PRIMARY,SPECIALIST]


def test_sdk_timeout_and_retry_controls_are_applied_per_request(monkeypatch):
    from agent.providers import GoogleGenAIProvider
    captured=[]
    def generate(**kwargs):
        captured.append(kwargs["config"].http_options)
        return SimpleNamespace(text="OK",usage_metadata=None)
    provider=GoogleGenAIProvider(RouterSettings(request_timeout_seconds=45,specialist_timeout_seconds=60))
    monkeypatch.setattr(provider,"_get_client",lambda:SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    provider.generate(PRIMARY,"request");provider.generate(SPECIALIST,"request")
    assert [options.timeout for options in captured]==[45000,60000]
    assert all(options.retry_options.attempts==1 for options in captured)
