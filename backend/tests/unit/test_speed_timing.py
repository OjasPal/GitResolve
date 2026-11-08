"""Timing must describe actual dispatch and preserve nested capture attribution."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from types import SimpleNamespace

from githubapp.timing import capture_timings, measure, model_dispatch_scope, model_dispatched
from app.worker.context import JobContext
from app.schemas.enums import EventType, TaskStage


def test_nested_and_explicit_background_capture():
    with capture_timings() as outer:
        with capture_timings() as inner:
            with measure("nested"):
                pass
        def background():
            with measure("background"):
                pass
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(copy_context().run, background).result()
    assert [r["operation"] for r in outer] == ["nested", "background"]
    assert len(inner) == 1
    with capture_timings() as other:
        pass
    assert other == []


def test_exception_span_and_scope_reset():
    with capture_timings() as records:
        try:
            with measure("failed"):
                raise ValueError("private content")
        except ValueError:
            pass
    assert records[0]["ok"] is False
    assert "private" not in str(records)
    calls = []
    with model_dispatch_scope(lambda: calls.append(True)):
        model_dispatched()
    model_dispatched()
    assert calls == [True]


def test_llm_event_is_not_dispatch_and_final_stage_flushes(caplog):
    svc = SimpleNamespace(append_event=lambda *a: None, update_progress=lambda *a, **kw: None,
                          heartbeat=lambda *a: False, lease_diagnostics=lambda *a: {})
    ctx = JobContext(svc, "task", "worker")
    with caplog.at_level("INFO", logger="gitresolve.performance"):
        ctx.event(EventType.LLM_CALL, "Preparing a request")
        assert not ctx._first_model
        with model_dispatch_scope(ctx.model_dispatched):
            model_dispatched()
            model_dispatched()
        ctx.stage(TaskStage.EVALUATE)
        ctx.flush_timing()
    assert sum(r.message == "first model dispatch" for r in caplog.records) == 1
    assert caplog.records[-1].stage == TaskStage.EVALUATE.value


def test_sdk_configuration_failure_does_not_report_a_model_dispatch(monkeypatch):
    import pytest
    from agent.providers import GoogleGenAIProvider, ProviderError
    from agent.config import RouterSettings
    provider = GoogleGenAIProvider(RouterSettings())
    invoked = []
    monkeypatch.setattr(provider, '_get_client', lambda: SimpleNamespace(models=SimpleNamespace(
        generate_content=lambda **kwargs: invoked.append(True))))
    def invalid(**kwargs): raise ValueError('Invalid local schema')
    monkeypatch.setattr('agent.providers.types.GenerateContentConfig', invalid)
    dispatched = []
    with model_dispatch_scope(lambda: dispatched.append(True)):
        with pytest.raises(ProviderError): provider.generate(model_id='fixture', prompt='schema')
    assert invoked == dispatched == []
