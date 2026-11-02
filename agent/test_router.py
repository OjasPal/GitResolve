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
from agent.repair import PatchProposal, execute_repair_step, should_escalate_to_opus
from agent.router import (
    AllProvidersFailedError,
    EscalationLimitExceededError,
    ModelRouter,
    RouterResult,
)
from agent.state import AgentState


@pytest.fixture(autouse=True)
def isolate_model_configuration(monkeypatch):
    """Routing fixtures must not inherit the developer's live fallback choices."""
    for key, value in {
        "LLM_PRIMARY_PROVIDER": "anthropic", "LLM_PRIMARY_MODEL": "claude-sonnet-5-5",
        "LLM_FALLBACK_1_PROVIDER": "google", "LLM_FALLBACK_1_MODEL": "gemini-4-argon",
        "LLM_FALLBACK_2_PROVIDER": "google", "LLM_FALLBACK_2_MODEL": "gemini-3.8-flash",
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
    anthropic_mock: MockProvider,
    google_mock: MockProvider,
    max_retries: int = 2,
    enable_failover: bool = True,
    enable_opus: bool = True,
    max_opus: int = 1,
) -> tuple[ModelRouter, list[float]]:
    sleep_calls: list[float] = []

    def mock_sleeper(duration: float):
        sleep_calls.append(duration)

    settings = RouterSettings(
        google_cloud_project="test-project",
        anthropic_vertex_project_id="test-project",
        max_provider_retries=max_retries,
        enable_failover=enable_failover,
        enable_opus_escalation=enable_opus,
        max_opus_escalations=max_opus,
    )

    def provider_factory(name: str) -> BaseProvider:
        if name == "anthropic":
            return anthropic_mock
        if name == "google":
            return google_mock
        raise ValueError(f"Unknown mock provider {name}")

    router = ModelRouter(
        settings=settings,
        provider_factory=provider_factory,
        sleeper=mock_sleeper,
    )
    return router, sleep_calls


# ==============================================================================
# 1. Sonnet succeeds -> no fallback
# ==============================================================================
def test_1_sonnet_succeeds_no_fallback():
    anthropic = MockProvider("anthropic")
    google = MockProvider("google")

    usage = create_usage_record("anthropic", "claude-sonnet-5-5", 500, 200, 50)
    analysis = IssueAnalysis(
        problem_summary="Valid summary",
        investigation_steps=["Investigate line 10"],
        needs_more_information=False,
        questions_for_reporter=[],
    )
    anthropic.queue_response(
        "claude-sonnet-5-5",
        ModelResult("anthropic", "claude-sonnet-5-5", parsed=analysis, usage=usage),
    )

    router, sleep_calls = create_test_router(anthropic, google)
    res = router.route_normal(prompt="test prompt", schema=IssueAnalysis)

    assert res.selected_provider == "anthropic"
    assert res.selected_model == "claude-sonnet-5-5"
    assert res.failover_occurred is False
    assert res.specialist_escalation_occurred is False
    assert res.provider_retries == 0
    assert len(google.call_history) == 0  # Fallback providers not called
    assert len(sleep_calls) == 0


# ==============================================================================
# 2. Sonnet encounters retryable technical failure -> Gemini 4 is selected
# ==============================================================================
def test_2_sonnet_retryable_failure_falls_back_to_gemini_4():
    anthropic = MockProvider("anthropic")
    google = MockProvider("google")

    # Sonnet fails with transient 429 RateLimitError across all retries (1 initial + 2 retries = 3 attempts)
    for _ in range(3):
        anthropic.queue_response(
            "claude-sonnet-5-5",
            RetryableProviderError("HTTP 429: Rate limit exceeded", "anthropic", "claude-sonnet-5-5", status_code=429),
        )

    # Gemini 4 Argon succeeds
    gemini_usage = create_usage_record("google", "gemini-4-argon", 400, 150)
    google.queue_response(
        "gemini-4-argon",
        ModelResult("google", "gemini-4-argon", parsed=IssueAnalysis(
            problem_summary="Gemini 4 plan",
            investigation_steps=["Step 1"],
            needs_more_information=False,
            questions_for_reporter=[],
        ), usage=gemini_usage),
    )

    router, sleep_calls = create_test_router(anthropic, google, max_retries=2)
    res = router.route_normal(prompt="test prompt", schema=IssueAnalysis)

    assert res.selected_provider == "google"
    assert res.selected_model == "gemini-4-argon"
    assert res.failover_occurred is True
    assert res.specialist_escalation_occurred is False
    assert res.provider_retries == 2  # 2 retries attempted on Sonnet before switching
    assert "Failover from anthropic/claude-sonnet-5-5" in res.model_switch_reason
    assert len(google.call_history) == 1
    assert google.call_history[0]["model_id"] == "gemini-4-argon"


# ==============================================================================
# 3. Gemini 4 also fails technically -> Gemini Flash is selected
# ==============================================================================
def test_3_gemini_4_also_fails_technically_falls_back_to_gemini_flash():
    anthropic = MockProvider("anthropic")
    google = MockProvider("google")

    # Sonnet fails (3 attempts)
    for _ in range(3):
        anthropic.queue_response(
            "claude-sonnet-5-5",
            RetryableProviderError("503 Service Unavailable", "anthropic", "claude-sonnet-5-5", status_code=503),
        )

    # Gemini 4 Argon also fails (3 attempts)
    for _ in range(3):
        google.queue_response(
            "gemini-4-argon",
            RetryableProviderError("500 Internal Server Error", "google", "gemini-4-argon", status_code=500),
        )

    # Gemini 3.8 Flash succeeds
    flash_usage = create_usage_record("google", "gemini-3.8-flash", 300, 100)
    google.queue_response(
        "gemini-3.8-flash",
        ModelResult("google", "gemini-3.8-flash", parsed=IssueAnalysis(
            problem_summary="Flash plan",
            investigation_steps=["Flash Step"],
            needs_more_information=False,
            questions_for_reporter=[],
        ), usage=flash_usage),
    )

    router, sleep_calls = create_test_router(anthropic, google, max_retries=2)
    res = router.route_normal(prompt="test prompt", schema=IssueAnalysis)

    assert res.selected_provider == "google"
    assert res.selected_model == "gemini-3.8-flash"
    assert res.failover_occurred is True
    assert res.provider_retries == 4  # 2 retries on Sonnet + 2 retries on Gemini 4


# ==============================================================================
# 4. All three providers fail -> clear, controlled failure returned
# ==============================================================================
def test_4_all_three_providers_fail_returns_controlled_error():
    anthropic = MockProvider("anthropic")
    google = MockProvider("google")

    # Sonnet fails
    for _ in range(3):
        anthropic.queue_response(
            "claude-sonnet-5-5",
            RetryableProviderError("Connection timeout", "anthropic", "claude-sonnet-5-5", status_code=504),
        )
    # Gemini 4 fails
    for _ in range(3):
        google.queue_response(
            "gemini-4-argon",
            RetryableProviderError("502 Bad Gateway", "google", "gemini-4-argon", status_code=502),
        )
    # Gemini Flash fails
    for _ in range(3):
        google.queue_response(
            "gemini-3.8-flash",
            RetryableProviderError("429 Quota Exceeded", "google", "gemini-3.8-flash", status_code=429),
        )

    router, _ = create_test_router(anthropic, google, max_retries=2)

    with pytest.raises(AllProvidersFailedError) as exc_info:
        router.route_normal(prompt="test prompt", schema=IssueAnalysis)

    err = exc_info.value
    assert "All configured providers failed in failover chain" in str(err)
    assert len(err.errors) == 9  # 3 attempts for each of the 3 models


# ==============================================================================
# 5. Sonnet returns incorrect patch -> no automatic provider failover
# ==============================================================================
def test_5_sonnet_returns_incorrect_patch_no_provider_failover():
    anthropic = MockProvider("anthropic")
    google = MockProvider("google")

    # Sonnet responds successfully at the provider level, but returns malformed patch content
    anthropic.queue_response(
        "claude-sonnet-5-5",
        ModelResult("anthropic", "claude-sonnet-5-5", parsed=None, raw_text="not a valid json patch"),
    )

    router, _ = create_test_router(anthropic, google)
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

    # Provider failover must NOT have occurred!
    assert len(google.call_history) == 0
    assert result["repair_attempts"] == 1
    assert "reasoning error, no provider failover" in result["error"]


# ==============================================================================
# 6. Tests fail after patch -> repair workflow continues without provider failover
# ==============================================================================
def test_6_tests_fail_after_patch_repair_workflow_continues():
    anthropic = MockProvider("anthropic")
    google = MockProvider("google")

    patch = PatchProposal(
        explanation="Attempted off-by-one fix",
        diff="--- a/main.py\n+++ b/main.py\n@@ -5,1 +5,1 @@\n-x = 0\n+x = 1",
        confidence=0.8,
        cross_file_impact=False,
    )
    anthropic.queue_response(
        "claude-sonnet-5-5",
        ModelResult("anthropic", "claude-sonnet-5-5", parsed=patch),
    )

    def failing_test_runner(diff: str) -> tuple[bool, str]:
        return False, "pytest: FAILED test_edge_case - AssertionError"

    router, _ = create_test_router(anthropic, google)
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
# 7. Explicit specialist escalation -> Opus is invoked
# ==============================================================================
def test_7_explicit_specialist_escalation_invokes_opus():
    anthropic = MockProvider("anthropic")
    google = MockProvider("google")

    opus_usage = create_usage_record("anthropic", "claude-opus-5-5", 1500, 800)
    opus_analysis = IssueAnalysis(
        problem_summary="Deep architectural root cause identified",
        investigation_steps=["Refactor interface layer", "Update all callers"],
        needs_more_information=False,
        questions_for_reporter=[],
    )
    anthropic.queue_response(
        "claude-opus-5-5",
        ModelResult("anthropic", "claude-opus-5-5", parsed=opus_analysis, usage=opus_usage),
    )

    router, _ = create_test_router(anthropic, google)
    res = router.escalate_specialist(
        prompt="Escalated reasoning task",
        escalation_reason="Complex cross-file concurrency bug",
        schema=IssueAnalysis,
    )

    assert res.selected_provider == "anthropic"
    assert res.selected_model == "claude-opus-5-5"
    assert res.specialist_escalation_occurred is True
    assert res.failover_occurred is False
    assert "Specialist reasoning escalation" in res.model_switch_reason
    assert len(google.call_history) == 0
    # Anthropic called with Opus 5.5, not Sonnet
    assert len(anthropic.call_history) == 1
    assert anthropic.call_history[0]["model_id"] == "claude-opus-5-5"


# ==============================================================================
# 8. Sonnet succeeds but workflow requests Opus escalation -> Opus invoked independently of failover
# ==============================================================================
def test_8_workflow_requests_opus_escalation_independent_of_failover():
    anthropic = MockProvider("anthropic")
    google = MockProvider("google")

    opus_patch = PatchProposal(
        explanation="Opus deep reasoning cross-file fix",
        diff="--- a/auth.py\n+++ b/auth.py\n@@ -1,1 +1,1 @@\n-v1\n+v2",
        confidence=0.95,
        cross_file_impact=True,
    )
    anthropic.queue_response(
        "claude-opus-5-5",
        ModelResult("anthropic", "claude-opus-5-5", parsed=opus_patch),
    )

    router, _ = create_test_router(anthropic, google)

    # State where repair_attempts >= 1 indicates previous repair failure, triggering escalation
    state: AgentState = {
        "task_id": "tsk-1",
        "repo_full_name": "owner/repo",
        "issue_number": 42,
        "issue_title": "Difficult issue",
        "issue_body": "Complex bug",
        "stage": "running",
        "problem_summary": "Summary",
        "likely_files": ["auth.py", "session.py"],
        "plan": ["fix"],
        "needs_more_information": False,
        "questions_for_reporter": [],
        "repair_attempts": 1,  # Triggers escalation
        "max_repair_attempts": 3,
        "status": "running",
        "error": "Previous test failure",
        "escalation_count": 0,
    }

    escalate, reason = should_escalate_to_opus(state)
    assert escalate is True

    result = execute_repair_step(state, router=router, test_runner=lambda diff: (True, "OK"))

    assert result["selected_model"] == "claude-opus-5-5"
    assert result["specialist_escalation_occurred"] is True
    assert result["failover_occurred"] is False
    assert result["status"] == "awaiting_approval"


# ==============================================================================
# 9. Opus is NOT invoked merely because Sonnet has a provider outage
# ==============================================================================
def test_9_opus_is_not_invoked_on_sonnet_outage():
    anthropic = MockProvider("anthropic")
    google = MockProvider("google")

    # Sonnet has a complete outage
    for _ in range(3):
        anthropic.queue_response(
            "claude-sonnet-5-5",
            RetryableProviderError("503 Service Unavailable", "anthropic", "claude-sonnet-5-5", status_code=503),
        )

    # Gemini 4 Argon succeeds
    google.queue_response(
        "gemini-4-argon",
        ModelResult("google", "gemini-4-argon", parsed=IssueAnalysis(
            problem_summary="Argon fix",
            investigation_steps=["Step"],
            needs_more_information=False,
            questions_for_reporter=[],
        )),
    )

    router, _ = create_test_router(anthropic, google)
    res = router.route_normal(prompt="test", schema=IssueAnalysis)

    assert res.selected_model == "gemini-4-argon"
    # Verify Opus was NEVER called during technical failover
    called_models = [c["model_id"] for c in anthropic.call_history]
    assert "claude-opus-5-5" not in called_models


# ==============================================================================
# 10. Authentication/configuration errors do not trigger blind failover
# ==============================================================================
def test_10_auth_and_config_errors_fail_fast_without_failover():
    anthropic = MockProvider("anthropic")
    google = MockProvider("google")

    # Sonnet throws a 401 Unauthorized / missing ADC error
    anthropic.queue_response(
        "claude-sonnet-5-5",
        FatalProviderError("401 Unauthorized: Invalid credentials", "anthropic", "claude-sonnet-5-5", status_code=401),
    )

    router, _ = create_test_router(anthropic, google)

    with pytest.raises(FatalProviderError) as exc_info:
        router.route_normal(prompt="test", schema=IssueAnalysis)

    assert exc_info.value.status_code == 401
    # Fallback to Google must NOT have happened!
    assert len(google.call_history) == 0


# ==============================================================================
# 11. Retry and escalation limits are respected
# ==============================================================================
def test_11_retry_and_escalation_limits_are_respected():
    anthropic = MockProvider("anthropic")
    google = MockProvider("google")

    # Test 11a: Provider retries do not exceed max_provider_retries (2 retries -> 3 calls)
    for _ in range(5):
        anthropic.queue_response(
            "claude-sonnet-5-5",
            RetryableProviderError("429 rate limit", "anthropic", "claude-sonnet-5-5", status_code=429),
        )
    google.queue_response(
        "gemini-4-argon",
        ModelResult("google", "gemini-4-argon", parsed=IssueAnalysis(
            problem_summary="OK", investigation_steps=["A"], needs_more_information=False, questions_for_reporter=[]
        )),
    )

    router, sleep_calls = create_test_router(anthropic, google, max_retries=2)
    router.route_normal(prompt="test", schema=IssueAnalysis)

    sonnet_calls = [c for c in anthropic.call_history if c["model_id"] == "claude-sonnet-5-5"]
    assert len(sonnet_calls) == 3  # 1 initial + 2 retries
    assert len(sleep_calls) == 2   # 2 sleep backoffs

    # Test 11b: Opus escalation limit is bounded (max_opus_escalations = 1)
    router2, _ = create_test_router(anthropic, google, max_opus=1)
    with pytest.raises(EscalationLimitExceededError):
        router2.escalate_specialist(
            prompt="test",
            escalation_reason="another escalation",
            schema=IssueAnalysis,
            current_escalation_count=1,  # Already at limit
        )


# ==============================================================================
# 12. Usage metadata is recorded correctly when available and unavailable
# ==============================================================================
def test_12_usage_and_cost_metadata_recorded_correctly():
    # 12a: When token usage is available
    usage = create_usage_record(
        provider="anthropic",
        model_id="claude-sonnet-5-5",
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
        model_id="gemini-4-argon",
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
