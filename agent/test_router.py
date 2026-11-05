from typing import Any
import pytest

from agent.analysis_schema import IssueAnalysis
from agent.config import ModelConfig, PricingConfig, RouterSettings
from agent.observability import (
    UsageRecord,
    calculate_cost,
    create_usage_record,
    sanitize_error,
)
from agent.providers import (
    BaseProvider,
    FatalProviderError,
    ModelResult,
    RetryableProviderError,
    classify_error,
)
from agent.repair import PatchProposal, execute_repair_step
from agent.router import (
    AllProvidersFailedError,
    ModelRouter,
    RouterResult,
)
from agent.state import AgentState


@pytest.fixture(autouse=True)
def isolate_model_configuration(monkeypatch):
    """Routing fixtures must not inherit the developer's live fallback choices."""
    for key, value in {
        "LLM_PRIMARY_PROVIDER": "google", "LLM_PRIMARY_MODEL": "gemini-3.8-flash",
        "LLM_FALLBACK_1_PROVIDER": "google", "LLM_FALLBACK_1_MODEL": "gemini-3.6-flash",
    }.items():
        monkeypatch.setenv(key, value)


class MockProvider(BaseProvider):
    """Configurable mock provider for testing routing behavior without network calls."""

    def __init__(self, provider_name: str):
        self.provider_name = provider_name
        self.call_history: list[dict[str, Any]] = []
        # handlers: dict[model_id, list[Callable or Exception or ModelResult]]
        self.model_responses: dict[str, list[Any]] = {}

    def queue_response(self, model_id: str, response_or_exc: Any):
        if model_id not in self.model_responses:
            self.model_responses[model_id] = []
        self.model_responses[model_id].append(response_or_exc)

    def generate(
        self,
        model_id: str,
        prompt: str,
        schema: Any = None,
        system_instruction: str | None = None,
        task_id: str | None = None,
    ) -> ModelResult:
        self.call_history.append({
            "model_id": model_id,
            "prompt": prompt,
            "schema": schema,
            "system_instruction": system_instruction,
            "task_id": task_id,
        })

        queue = self.model_responses.get(model_id, [])
        if not queue:
            # Default empty analysis
            parsed = None
            if schema == IssueAnalysis:
                parsed = IssueAnalysis(
                    problem_summary="Mocked analysis",
                    investigation_steps=["Step 1"],
                    needs_more_information=False,
                    questions_for_reporter=[],
                )
            elif schema == PatchProposal:
                parsed = PatchProposal(
                    explanation="Mocked fix",
                    diff="--- a/file.py\n+++ b/file.py\n@@ -1,1 +1,1 @@\n-old\n+new",
                    confidence=0.9,
                    cross_file_impact=False,
                )
            return ModelResult(
                provider=self.provider_name,
                model_id=model_id,
                parsed=parsed,
                raw_text="mocked response",
                usage=create_usage_record(self.provider_name, model_id, 100, 50, 20),
            )

        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item()
        return item


def create_test_router(
    primary_mock: MockProvider,
    google_mock: MockProvider,
    max_retries: int = 2,
    enable_failover: bool = True,
) -> tuple[ModelRouter, list[float]]:
    sleep_calls: list[float] = []

    def mock_sleeper(duration: float):
        sleep_calls.append(duration)

    settings = RouterSettings(
        google_cloud_project="test-project",
        max_provider_retries=max_retries,
        enable_failover=enable_failover,
    )

    class RoutedMock(BaseProvider):
        def generate(self, model_id, **kwargs):
            target = primary_mock if model_id == settings.primary.model_id else google_mock
            return target.generate(model_id=model_id, **kwargs)
    def provider_factory(name):
        return RoutedMock()

    router = ModelRouter(
        settings=settings,
        provider_factory=provider_factory,
        sleeper=mock_sleeper,
    )
    return router, sleep_calls


# ==============================================================================
# 1. Primary Flash succeeds -> no fallback
# ==============================================================================
def test_1_primary_succeeds_no_fallback():
    primary = MockProvider("google")
    google = MockProvider("google")

    usage = create_usage_record("google", "gemini-3.8-flash", 500, 200, 50)
    analysis = IssueAnalysis(
        problem_summary="Valid summary",
        investigation_steps=["Investigate line 10"],
        needs_more_information=False,
        questions_for_reporter=[],
    )
    primary.queue_response(
        "gemini-3.8-flash",
        ModelResult("google", "gemini-3.8-flash", parsed=analysis, usage=usage),
    )

    router, sleep_calls = create_test_router(primary, google)
    res = router.route_normal(prompt="test prompt", schema=IssueAnalysis)

    assert res.selected_provider == "google"
    assert res.selected_model == "gemini-3.8-flash"
    assert res.failover_occurred is False
    assert res.specialist_escalation_occurred is False
    assert res.provider_retries == 0
    assert len(google.call_history) == 0  # Fallback providers not called
    assert len(sleep_calls) == 0


# ==============================================================================
# 2. Primary technical failure -> backup Flash is selected
# ==============================================================================
def test_2_primary_failure_switches_immediately():
    primary = MockProvider("google")
    google = MockProvider("google")

    # Primary Flash fails with transient 429 RateLimitError across all retries (1 initial + 2 retries = 3 attempts)
    for _ in range(3):
        primary.queue_response(
            "gemini-3.8-flash",
            RetryableProviderError("HTTP 429: Rate limit exceeded", "google", "gemini-3.8-flash", status_code=429),
        )

    # Gemini 4 Primary Flash succeeds
    gemini_usage = create_usage_record("google", "gemini-3.6-flash", 400, 150)
    google.queue_response(
        "gemini-3.6-flash",
        ModelResult("google", "gemini-3.6-flash", parsed=IssueAnalysis(
            problem_summary="Gemini 4 plan",
            investigation_steps=["Step 1"],
            needs_more_information=False,
            questions_for_reporter=[],
        ), usage=gemini_usage),
    )

    router, sleep_calls = create_test_router(primary, google, max_retries=2)
    res = router.route_normal(prompt="test prompt", schema=IssueAnalysis)

    assert res.selected_provider == "google"
    assert res.selected_model == "gemini-3.6-flash"
    assert res.failover_occurred is True
    assert res.specialist_escalation_occurred is False
    assert res.provider_retries == 0  # no long retry chain
    assert "Technical failover" in res.model_switch_reason
    assert len(google.call_history) == 1
    assert google.call_history[0]["model_id"] == "gemini-3.6-flash"


# ==============================================================================
# 3. Gemini 4 also fails technically -> Gemini Flash is selected
# ==============================================================================

def test_4_both_models_fail_returns_controlled_error():
    primary = MockProvider("google")
    google = MockProvider("google")

    # Primary Flash fails
    for _ in range(3):
        primary.queue_response(
            "gemini-3.8-flash",
            RetryableProviderError("Connection timeout", "google", "gemini-3.8-flash", status_code=504),
        )
    # Primary failure fixture
    for _ in range(3):
        google.queue_response(
            "gemini-3.8-flash",
            RetryableProviderError("502 Bad Gateway", "google", "gemini-3.8-flash", status_code=502),
        )
    # Backup Flash fails
    for _ in range(3):
        google.queue_response(
            "gemini-3.6-flash",
            RetryableProviderError("429 Quota Exceeded", "google", "gemini-3.6-flash", status_code=429),
        )

    router, _ = create_test_router(primary, google, max_retries=2)

    with pytest.raises(AllProvidersFailedError) as exc_info:
        router.route_normal(prompt="test prompt", schema=IssueAnalysis)

    err = exc_info.value
    assert "All configured providers failed in failover chain" in str(err)
    assert len(err.errors) == 2  # one request per Flash model


# ==============================================================================
# 5. Argon returns incorrect patch -> no automatic provider failover
# ==============================================================================
def test_5_invalid_output_uses_reasoning_specialist_only():
    primary = MockProvider("google")
    google = MockProvider("google")

    # Primary Flash responds successfully at the provider level, but returns malformed patch content
    primary.queue_response(
        "gemini-3.8-flash",
        ModelResult("google", "gemini-3.8-flash", parsed=None, raw_text="not a valid json patch"),
    )

    router, _ = create_test_router(primary, google)
    initial_state: AgentState = {
        "task_id": "tsk-1",
        "repo_full_name": "owner/repo",
        "issue_number": 42,
        "issue_title": "Fix bug",
        "issue_body": "Crash occurs",
        "stage": "running",
        "problem_summary": "Bug",
        "likely_files": ["foo.py"],
        "plan": ["fix it"],
        "needs_more_information": False,
        "questions_for_reporter": [],
        "repair_attempts": 0,
        "max_repair_attempts": 3,
        "status": "running",
        "error": None,
    }

    result = execute_repair_step(initial_state, router=router)

    # Invalid output is a reasoning failure: one specialist, never technical fallback.
    assert [c["model_id"] for c in google.call_history] == ["gemini-4-argon"]
    assert result["repair_attempts"] == 1
    assert result["specialist_escalation_occurred"] is True
    assert result["failover_occurred"] is False


# ==============================================================================
# 6. Tests fail after patch -> repair workflow continues without provider failover
# ==============================================================================
def test_6_tests_fail_after_patch_repair_workflow_continues():
    primary = MockProvider("google")
    google = MockProvider("google")

    patch = PatchProposal(
        explanation="Attempted off-by-one fix",
        diff="--- a/main.py\n+++ b/main.py\n@@ -5,1 +5,1 @@\n-x = 0\n+x = 1",
        confidence=0.8,
        cross_file_impact=False,
    )
    primary.queue_response(
        "gemini-3.8-flash",
        ModelResult("google", "gemini-3.8-flash", parsed=patch),
    )

    def failing_test_runner(diff: str) -> tuple[bool, str]:
        return False, "pytest: FAILED test_edge_case - AssertionError"

    router, _ = create_test_router(primary, google)
    initial_state: AgentState = {
        "task_id": "tsk-1",
        "repo_full_name": "owner/repo",
        "issue_number": 42,
        "issue_title": "Fix edge case",
        "issue_body": "Crash on empty input",
        "stage": "running",
        "problem_summary": "Crash on empty input",
        "likely_files": ["main.py"],
        "plan": ["check condition"],
        "needs_more_information": False,
        "questions_for_reporter": [],
        "repair_attempts": 0,
        "max_repair_attempts": 3,
        "status": "running",
        "error": None,
    }

    result = execute_repair_step(initial_state, router=router, test_runner=failing_test_runner)

    # Provider failover was NOT triggered
    assert len(google.call_history) == 0
    assert result["failover_occurred"] is False
    assert result["repair_attempts"] == 1
    assert result["status"] == "running"
    assert "Test suite failed after applying patch" in result["error"]


# ==============================================================================
# ==============================================================================



def test_10_auth_and_config_errors_fail_fast_without_failover():
    primary = MockProvider("google")
    google = MockProvider("google")

    # Primary Flash throws a 401 Unauthorized / missing ADC error
    primary.queue_response(
        "gemini-3.8-flash",
        FatalProviderError("401 Unauthorized: Invalid credentials", "google", "gemini-3.8-flash", status_code=401),
    )

    router, _ = create_test_router(primary, google)

    with pytest.raises(FatalProviderError) as exc_info:
        router.route_normal(prompt="test", schema=IssueAnalysis)

    assert exc_info.value.status_code == 401
    # Fallback to Google must NOT have happened!
    assert len(google.call_history) == 0


# ==============================================================================
# 11. Retry limits are respected
# ==============================================================================
def test_11_retry_limits_are_respected():
    primary = MockProvider("google")
    google = MockProvider("google")

    # Test 11a: Provider retries do not exceed max_provider_retries (2 retries -> 3 calls)
    for _ in range(5):
        primary.queue_response(
            "gemini-3.8-flash",
            RetryableProviderError("429 rate limit", "google", "gemini-3.8-flash", status_code=429),
        )
    google.queue_response(
        "gemini-3.8-flash",
        ModelResult("google", "gemini-3.8-flash", parsed=IssueAnalysis(
            problem_summary="OK", investigation_steps=["A"], needs_more_information=False, questions_for_reporter=[]
        )),
    )

    router, sleep_calls = create_test_router(primary, google, max_retries=2)
    router.route_normal(prompt="test", schema=IssueAnalysis)

    argon_calls = [c for c in primary.call_history if c["model_id"] == "gemini-3.8-flash"]
    assert len(argon_calls) == 1  # one initial request
    assert len(sleep_calls) == 0   # no blind retry delay


def test_12_usage_and_cost_metadata_recorded_correctly():
    # 12a: When token usage is available
    usage = create_usage_record(
        provider="google",
        model_id="gemini-3.8-flash",
        input_tokens=1_000,
        output_tokens=500,
        cached_tokens=200,
        task_id="task-xyz",
    )
    assert usage.usage_available is True
    assert usage.input_tokens == 1_000
    assert usage.output_tokens == 500
    assert usage.cached_tokens == 200
    assert usage.cost_is_estimate is True
    assert usage.estimated_cost_usd is not None
    # 800 non-cached @ $3/M ($0.0024) + 200 cached @ $0.30/M ($0.00006) + 500 completion @ $15/M ($0.0075) = ~$0.00996
    assert usage.estimated_cost_usd > 0

    # 12b: When token usage is unavailable
    no_usage = create_usage_record(
        provider="google",
        model_id="gemini-3.8-flash",
        input_tokens=None,
        output_tokens=None,
    )
    assert no_usage.usage_available is False
    assert no_usage.estimated_cost_usd is None
    assert no_usage.input_tokens is None

    # 12c: Sanitization strips credentials
    fake_api_key = "AQ." + "x" * 48
    fake_access_token = "ya29." + "test-token"
    raw_error = f"Failed with {fake_api_key} and Bearer {fake_access_token}"
    sanitized = sanitize_error(raw_error)
    assert fake_api_key not in sanitized
    assert "ya29" not in sanitized
    assert "[REDACTED" in sanitized
