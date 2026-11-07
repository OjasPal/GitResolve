"""Phase 3 deterministic unit & Adaptive Lanes (AL) evaluation tests (0 live LLM calls).

Covers:
1. Single-taxonomy failure classifier mapping sandbox/classification.py + AL EscalationReason + AttemptMemory
2. Strategy router (missing_dep -> env_prepare, anchor_mismatch, no_repro, logic_fail, regression_elsewhere, timeout_flaky, policy_block, infra)
3. Argon (gemini-4-argon) reflect-and-replan triggers, compact trajectory by reference, persisted per-task cap across resume
4. Google GenAI-only provider routing, per-model circuit breakers, fast-lane failover & pause-in-lane, zero Groq references
5. Per-node reasoning profiles and deadline-bounded timeouts (LOW in fast lane, MEDIUM/HIGH in full lane)
6. Optional best-of-N via LangGraph Send (full lane only, isolated overlays, single compact winner in checkpoint)
7. AL evaluation harness with provider-failure injection (0 unsafe-fast cases)
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent.attempt_memory import (
    ESCALATION_REASON_TO_FAILURE_CATEGORY,
    SANDBOX_CLASS_TO_FAILURE_CATEGORY,
    AttemptMemory,
    FailureCategory,
    category,
    classify_failure,
)
from agent.config import ModelConfig, RouterSettings, resolve_node_profile
from agent.health import ProviderHealth
from agent.observability import create_usage_record
from agent.providers import (
    BaseProvider,
    FatalProviderError,
    ModelResult,
    RetryableProviderError,
    UnknownProviderError,
    classify_error,
)
from agent.router import AllProvidersFailedError, ModelRouter
from app.agent.graph_engine.checkpointer import SqliteRepairCheckpointer, make_thread_id
from app.agent.graph_engine.engine import SpecialistAdvice, run_repair_graph
from app.agent.graph_engine.state import (
    MAX_CHECKPOINT_BYTES,
    MAX_PRESERVED_EVIDENCE_BYTES,
    MAX_SPECIALIST_CALLS_PER_TASK,
    validate_checkpoint_payload,
)
from app.agent.lanes import (
    EscalationReason,
    LaneName,
    ORACLE_GATE_MARKER,
    ORACLE_ORIG_MARKER,
    plan_verification,
    triage_repository,
)
from app.agent.repair_runner import (
    EditProposal,
    FastEditProposal,
    QualityReview,
    RepairProposal,
    run_repair,
)
from app.schemas.enums import FailureCode
from sandbox.classification import INFRA_CATEGORIES, classify as classify_sandbox
from sandbox.profiler import profile_repository

PRIMARY = "gemini-3.8-flash"
FALLBACK = "gemini-3.6-flash"
SPECIALIST = "gemini-4-argon"
BASE_SHA = "c" * 40
LIMITS = {
    "maxRepairAttempts": 4,
    "maxDiffFiles": 5,
    "maxDiffLines": 150,
    "maxModelCalls": 40,
    "taskTimeoutSeconds": 1800,
}


def _exec_result(
    code: int,
    stdout: str = "",
    stderr: str = "",
    *,
    timed_out: bool = False,
    oom_killed: bool = False,
    cleanup_ok: bool = True,
    error_code: str | None = None,
    exec_id: str = "exec_docker_1",
) -> SimpleNamespace:
    return SimpleNamespace(
        status="timed_out" if timed_out else ("passed" if code == 0 else "failed"),
        exit_code=code,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        oom_killed=oom_killed,
        cleanup_ok=cleanup_ok,
        error_code=error_code,
        execution_id=exec_id,
    )


def _approved_review(msg: str = "fix: resolve issue", branch: str = "fix-issue") -> QualityReview:
    return QualityReview(
        commit_message=msg,
        branch_name=branch,
        approved=True,
        requirements=[
            {
                "requirement": "Defect resolved and verified in Docker.",
                "status": "passed",
                "evidence": "Verified in isolated container.",
            }
        ],
        blocking_issues=[],
    )


class Phase3Context:
    def __init__(self, root: Path, results: list[Any]) -> None:
        self.root = Path(root)
        self.results = list(results)
        self.commands: list[dict[str, Any]] = []
        self.events: list[tuple[Any, str, dict[str, Any]]] = []
        self.stages: list[Any] = []
        self.artifacts: dict[str, str] = {}
        self.attempt_count = 0
        self.env_preparations: list[list[str]] = []
        self.lane_states: list[dict[str, Any]] = []
        self.budgets: dict[str, Any] = {"modelCalls": 0, "inputTokens": 0, "outputTokens": 0}
        self._svc = SimpleNamespace(
            store=SimpleNamespace(
                get=lambda path: (
                    {"budgets": self.budgets.copy(), "laneState": self.lane_states[-1] if self.lane_states else {}}
                    if path.startswith("tasks/") and "/artifacts/" not in path
                    else (
                        {"content": self.artifacts[path.split("/artifacts/")[1]]}
                        if "/artifacts/" in path and path.split("/artifacts/")[1] in self.artifacts
                        else None
                    )
                )
            )
        )

    def checkpoint(self) -> None:
        pass

    def prepare_environment(self, deps: list[str]) -> None:
        self.env_preparations.append(list(deps))

    def record_lane_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.lane_states.append(dict(payload))
        return payload

    def usage(self, attempts_used: int | None = None, **kw: Any) -> None:
        self.budgets.update(kw)
        if attempts_used is not None:
            self.attempt_count = attempts_used

    def stage(self, stage: Any, **kw: Any) -> None:
        self.stages.append(stage)

    def event(self, kind: Any, message: str, **kw: Any) -> None:
        self.events.append((kind, message, kw.get("data") or {}))

    def artifact(self, artifact_id: str, type_: Any, content: str, attempt: int | None = None) -> None:
        self.artifacts[artifact_id] = content

    @contextmanager
    def repository(self):
        yield self.root

    def execute(self, **kw: Any) -> Any:
        self.commands.append(kw)
        if not self.results:
            return _exec_result(0, "1 passed")
        return self.results.pop(0)


# --- 1. Single-Source-of-Truth Failure Classifier & Zero Groq References ---


def test_classifier_maps_sandbox_and_lane_outputs_single_taxonomy() -> None:
    # Every sandbox/classification.py output maps cleanly into FailureCategory
    sb_cases = [
        (_exec_result(2, "ModuleNotFoundError: No module named 'pydantic'"), "missing_dep", FailureCategory.ENV_MISSING_DEP.value),
        (_exec_result(1, "", "InvalidRequirement: bad spec", error_code="invalid_dependency_spec"), "invalid_dependency_spec", FailureCategory.ENV_MISSING_DEP.value),
        (_exec_result(1, "KeyError: 'OPENAI_API_KEY'"), "missing_secret", FailureCategory.ENV_MISSING_SECRET.value),
        (_exec_result(125, "docker error", cleanup_ok=False), "sandbox_prep_failure", FailureCategory.SANDBOX_INFRA.value),
        (_exec_result(1, "timed out", timed_out=True), "timeout_resource", FailureCategory.TIMEOUT_FLAKY.value),
        (_exec_result(5, "no tests ran"), "no_tests", FailureCategory.NO_REPRO.value),
        (_exec_result(2, "ERROR collecting test_app.py"), "collection_error", FailureCategory.NO_REPRO.value),
        (_exec_result(1, "FAILED test_app.py::test_x - AssertionError"), "preexisting_test_failure", FailureCategory.LOGIC_FAIL.value),
    ]
    for res, expected_sb, expected_cat in sb_cases:
        assert classify_sandbox(res) == expected_sb
        assert classify_failure(exec_result=res, stage="reproduce") == expected_cat
        assert SANDBOX_CLASS_TO_FAILURE_CATEGORY[expected_sb] is not None

    # Every AL EscalationReason value maps into FailureCategory without duplicating EscalationReason
    for reason in EscalationReason:
        mapped = classify_failure(escalation_reason=reason)
        assert mapped == ESCALATION_REASON_TO_FAILURE_CATEGORY[reason.value]
        assert mapped in {c.value for c in FailureCategory}

    # Regression elsewhere when unrelated test file fails outside target_test_files
    unrelated_res = _exec_result(1, "FAILED tests/test_billing.py::test_charge - AssertionError")
    assert (
        classify_failure(
            exec_result=unrelated_res,
            stage="verify",
            target_test_files=("tests/test_auth.py",),
        )
        == FailureCategory.REGRESSION_ELSEWHERE.value
    )

    # Static oracle failure on V0/V1 output
    oracle_fail_res = _exec_result(1, "ORACLE_V0_NO_DEFECT\n")
    assert classify_failure(exec_result=oracle_fail_res) == FailureCategory.STATIC_ORACLE_FAIL.value

    # Anchor mismatch, tool_invalid, policy_block, provider_unavailable
    assert category("Edit old_text did not match original source lines") == FailureCategory.ANCHOR_MISMATCH.value
    assert category("Unsupported argument for tool read_file") == FailureCategory.TOOL_INVALID.value
    assert category("Diff exceeds max changed lines scope") == FailureCategory.POLICY_BLOCK.value
    assert category("All configured providers failed; rate limit") == FailureCategory.PROVIDER_UNAVAILABLE.value

    # AttemptMemory records structured fields and detects persistent + consecutive logic failures
    mem = AttemptMemory()
    prop1 = EditProposal(
        explanation="Try changing plus to minus",
        tests=[{"path": "test_a.py", "content": "def test_a(): assert False\n"}],
        edits=[{"path": "a.py", "old_text": "x = 1", "new_text": "x = 2"}],
    )
    rec1 = mem.record(prop1, "FAILED test_a.py::test_a - AssertionError: 2 != 3", stage="verify")
    assert rec1["category"] == FailureCategory.LOGIC_FAIL.value
    assert rec1["what_was_tried"] == "Try changing plus to minus"
    assert rec1["evidence"]
    assert rec1["why_it_failed"].startswith("logic_fail:")
    assert rec1["rejected_hypothesis"]
    assert rec1["required_change"]

    prop2 = EditProposal(
        explanation="Try changing x to 4",
        tests=[{"path": "test_a.py", "content": "def test_a(): assert False\n"}],
        edits=[{"path": "a.py", "old_text": "x = 1", "new_text": "x = 4"}],
    )
    mem.record(prop2, "FAILED test_a.py::test_a - AssertionError: 4 != 3", stage="verify")
    assert mem.persistent() is True
    assert mem.consecutive_logic_failures() == 2
    assert len(mem.compact_trajectory()) == 2


def test_no_groq_references_remain_in_code_env_or_trd() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    paths_to_check = [
        repo_root / ".env.example",
        repo_root / "docs" / "GitResolve_TRD.md",
        *list((repo_root / "agent").rglob("*.py")),
        *list((repo_root / "backend" / "app").rglob("*.py")),
    ]
    for file_path in paths_to_check:
        if file_path.is_file():
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            assert "groq" not in content.lower(), f"Found forbidden Groq reference in {file_path}"


# --- 2. Argon Escalation on 2 Consecutive Logic Failures & Duplicate Patch Detection ---


def test_repeated_logic_failure_triggers_argon_with_evidence_and_duplicate_patch_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_ENGINE", "graph")
    monkeypatch.setenv("ADAPTIVE_LANES", "off")
    (tmp_path / "calc.py").write_text("def compute(x):\n    return x - 1\n", encoding="utf-8")

    bad_prop_1 = EditProposal(
        explanation="Use x + 2",
        tests=[{"path": "test_calc.py", "content": "from calc import compute\ndef test_c():\n    assert compute(3) == 4\n"}],
        edits=[{"path": "calc.py", "old_text": "    return x - 1\n", "new_text": "    return x + 2\n"}],
    )
    bad_prop_2 = EditProposal(
        explanation="Use x * 2",
        tests=[{"path": "test_calc.py", "content": "from calc import compute\ndef test_c():\n    assert compute(3) == 4\n"}],
        edits=[{"path": "calc.py", "old_text": "    return x - 1\n", "new_text": "    return x * 2\n"}],
    )
    good_prop_3 = EditProposal(
        explanation="Use x + 1 as recommended by Argon specialist",
        tests=[{"path": "test_calc.py", "content": "from calc import compute\ndef test_c():\n    assert compute(3) == 4\n"}],
        edits=[{"path": "calc.py", "old_text": "    return x - 1\n", "new_text": "    return x + 1\n"}],
    )
    argon_advice = SpecialistAdvice(
        root_cause_diagnosis="Prior attempts added 2 or multiplied by 2 instead of incrementing by 1.",
        rejected_hypotheses=["x + 2", "x * 2"],
        recommended_strategy="Replace `return x - 1` with `return x + 1` in calc.py.",
        target_files=["calc.py"],
    )

    normal_replies = iter([bad_prop_1, bad_prop_2, good_prop_3, _approved_review("fix: increment x by 1 in compute")])
    specialist_calls: list[dict[str, Any]] = []
    normal_calls: list[dict[str, Any]] = []

    class ScriptedRouter:
        def route_normal(self, prompt: str, **kwargs: Any) -> Any:
            normal_calls.append({"prompt": prompt, "kwargs": kwargs})
            parsed = next(normal_replies)
            return SimpleNamespace(
                result=SimpleNamespace(parsed=parsed, raw_text=""),
                usage=SimpleNamespace(input_tokens=100, output_tokens=40),
                selected_provider="google",
                selected_model=PRIMARY,
            )

        def route_specialist(self, prompt: str, **kwargs: Any) -> Any:
            specialist_calls.append({"prompt": prompt, "kwargs": kwargs})
            return SimpleNamespace(
                result=SimpleNamespace(parsed=argon_advice, raw_text=""),
                usage=SimpleNamespace(input_tokens=180, output_tokens=60),
                selected_provider="google",
                selected_model=SPECIALIST,
            )

    ctx = Phase3Context(
        tmp_path,
        [
            _exec_result(0, "1 passed"),  # baseline
            _exec_result(1, "1 failed"),  # attempt 1 reproduce
            _exec_result(1, "FAILED test_calc.py::test_c - AssertionError: 5 != 4"),  # attempt 1 verify fail (logic_fail #1)
            _exec_result(1, "1 failed"),  # attempt 2 reproduce
            _exec_result(1, "FAILED test_calc.py::test_c - AssertionError: 6 != 4"),  # attempt 2 verify fail (logic_fail #2)
            _exec_result(1, "1 failed"),  # attempt 3 reproduce
            _exec_result(0, "2 passed"),  # attempt 3 verify pass
        ],
    )
    task = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_argon_2_logic",
        limits=LIMITS,
        attempts_used=0,
        issue={"title": "compute(x) should return x + 1", "body": "Fix compute in calc.py"},
        plan={},
    )

    outcome = run_repair(task, ctx, ScriptedRouter())
    assert outcome.outcome == "fix_ready"
    assert "+    return x + 1" in outcome.final_diff
    # Argon specialist was invoked exactly once after 2 consecutive logic failures
    assert len(specialist_calls) == 1
    spec_prompt = specialist_calls[0]["prompt"]
    assert "two_consecutive_logic_failures" in spec_prompt
    assert "triageRef=sha256:" in spec_prompt
    assert "Compact attempt trajectory:" in spec_prompt
    assert "Remaining task budgets:" in spec_prompt
    # Attempt 3 patch prompt received Argon's diagnosis and HIGH reasoning profile (patch_retry)
    assert "Specialist (Argon) root-cause diagnosis and replan:" in normal_calls[2]["prompt"]
    assert normal_calls[0]["kwargs"]["thinking_level"] == "MEDIUM"
    assert normal_calls[1]["kwargs"]["thinking_level"] == "HIGH"
    assert normal_calls[2]["kwargs"]["thinking_level"] == "HIGH"


# --- 3. Provider Breakers, Failover, Unknown Error Fallback, and Global Outage Pause/Requeue ---


def test_per_model_breakers_failover_and_specialist_isolation() -> None:
    clock = [1000.0]
    health = ProviderHealth(clock=lambda: clock[0], breaker_threshold=2, cooldown=60.0)
    calls: list[tuple[str, str | None, float | None]] = []

    class ScriptedProvider(BaseProvider):
        def __init__(self, handlers: dict[str, list[Any]]) -> None:
            self.handlers = handlers

        def generate(
            self,
            model_id: str,
            prompt: str,
            schema: Any = None,
            system_instruction: str | None = None,
            task_id: str | None = None,
            *,
            thinking_level: str | None = None,
            timeout_seconds: float | None = None,
        ) -> ModelResult:
            calls.append((model_id, thinking_level, timeout_seconds))
            q = self.handlers.get(model_id, [])
            if not q:
                return ModelResult("google", model_id, raw_text="ok", usage=create_usage_record("google", model_id, 50, 20))
            item = q.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    # 1. Open breaker on primary (gemini-3.8-flash): fallback (gemini-3.6-flash) and specialist (gemini-4-argon) remain available!
    health.open_breaker(PRIMARY, duration=60.0)
    assert not health.available(PRIMARY)
    assert health.available(FALLBACK)
    assert health.available(SPECIALIST)
    assert not health.blocked()

    provider = ScriptedProvider({})
    router = ModelRouter(
        RouterSettings(google_cloud_project="test-proj"),
        provider_factory=lambda _: provider,
        health=health,
    )

    # Normal call skips open primary breaker and succeeds on fallback_1
    res_fallback = router.route_normal("hello", lane="full", node="explore", thinking_level="MEDIUM", timeout_seconds=35.0)
    assert res_fallback.selected_model == FALLBACK
    assert res_fallback.failover_occurred is True

    # Specialist call succeeds even though primary breaker is open!
    res_spec = router.route_specialist("diagnose trajectory", lane="full", node="specialist_reflect")
    assert res_spec.selected_model == SPECIALIST
    assert res_spec.specialist_escalation_occurred is True

    # 2. Open specialist breaker: fast lane and primary/fallback chain are completely unaffected!
    health.open_breaker(SPECIALIST, duration=60.0)
    clock[0] += 65.0  # primary cooldown expires
    health.open_breaker(SPECIALIST, duration=60.0)
    assert not health.available(SPECIALIST)
    assert health.available(PRIMARY)
    assert not health.blocked()

    res_fast = router.route_normal("fast patch", lane="fast", node="fast_patch_action")
    assert res_fast.selected_model == PRIMARY
    assert res_fast.reasoning_profile == "LOW"

    # 3. Unknown error gets fallback to 3.6-flash (not instant fatal), while 400/401 are FatalProviderError
    unk_err = classify_error(RuntimeError("unexpected serialization hiccup"), "google", PRIMARY)
    assert isinstance(unk_err, UnknownProviderError)
    assert unk_err.error_category == "unknown"

    auth_err = classify_error(RuntimeError("401 unauthenticated: invalid credentials"), "google", PRIMARY)
    assert isinstance(auth_err, FatalProviderError)
    assert auth_err.error_category == "auth"

    cfg_err = classify_error(RuntimeError("404 model not found: models/unknown"), "google", PRIMARY)
    assert isinstance(cfg_err, FatalProviderError)
    assert cfg_err.error_category == "invalid_config"

    provider_unk = ScriptedProvider({PRIMARY: [unk_err], FALLBACK: []})
    router_unk = ModelRouter(
        RouterSettings(google_cloud_project="test-proj"),
        provider_factory=lambda _: provider_unk,
        health=ProviderHealth(),
    )
    res_after_unk = router_unk.route_normal("recover from unknown error")
    assert res_after_unk.selected_model == FALLBACK
    assert res_after_unk.failover_occurred is True
    assert any(t["failureClass"] == "unknown" for t in router_unk.telemetry_log)


# --- 4. Infra/Env Failures Consume Zero Attempts & Strategy Router Edges ---


def test_infra_and_env_failures_consume_zero_attempts_and_missing_dep_routes_to_env_prepare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_ENGINE", "graph")
    monkeypatch.setenv("ADAPTIVE_LANES", "off")
    (tmp_path / "requirements.txt").write_text("pytest==8.0.0\n", encoding="utf-8")
    (tmp_path / "calc.py").write_text("def inc(x):\n    return x - 1\n", encoding="utf-8")

    proposal = EditProposal(
        explanation="Fix inc(x) to return x + 1",
        tests=[{"path": "test_calc.py", "content": "from calc import inc\ndef test_inc():\n    assert inc(1) == 2\n"}],
        edits=[{"path": "calc.py", "old_text": "    return x - 1\n", "new_text": "    return x + 1\n"}],
    )

    class SimpleRouter:
        def route_normal(self, prompt: str, **kw: Any) -> Any:
            schema = kw.get("schema")
            parsed = _approved_review() if schema is QualityReview else proposal
            return SimpleNamespace(
                result=SimpleNamespace(parsed=parsed, raw_text=""),
                usage=SimpleNamespace(input_tokens=60, output_tokens=25),
                selected_provider="google",
                selected_model=PRIMARY,
            )

    # Reproduction fails first with missing_dep -> routes to env_prepare (0 attempts consumed) -> re-runs reproduce
    # Then verify fails once with timeout_resource -> controlled rerun in verify (0 attempts consumed) -> passes!
    ctx = Phase3Context(
        tmp_path,
        [
            _exec_result(0, "1 passed"),  # baseline
            _exec_result(2, "ModuleNotFoundError: No module named 'pytest_cov'"),  # reproduce #1: missing_dep
            _exec_result(1, "1 failed"),  # reproduce #2 after env_prepare: reproduces!
            _exec_result(1, "timed out", timed_out=True),  # verify #1: flaky timeout
            _exec_result(0, "2 passed"),  # verify #2 controlled rerun: passes!
        ],
    )
    task = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_env_and_flaky",
        limits=LIMITS,
        attempts_used=0,
        issue={"title": "inc(x) subtracts 1", "body": "Fix inc in calc.py"},
        plan={},
    )

    outcome = run_repair(task, ctx, SimpleRouter())
    assert outcome.outcome == "fix_ready"
    # env_prepare hook was called once for missing_dep, and only 1 repair attempt was charged at final review!
    assert len(ctx.env_preparations) == 1
    assert ctx.attempt_count == 1


# --- 5. Fast Lane Rules: Never Calls Specialist, Escalates on Failure, Failover & Pause-in-Lane ---


def test_fast_lane_never_calls_specialist_and_failures_escalate_without_consuming_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_ENGINE", "graph")
    monkeypatch.setenv("ADAPTIVE_LANES", "on")
    orig_code = "import math\n\ndef tags(s: str) -> list[str]:\n    return re.findall(r'#(\\w+)', s)\n"
    (tmp_path / "prompcraft.py").write_text(orig_code, encoding="utf-8")

    # 1. ModelRouter directly forbids specialist in fast lane
    router_direct = ModelRouter(RouterSettings(google_cloud_project="test-proj"))
    with pytest.raises(ValueError, match="never permitted to invoke the specialist"):
        router_direct.route_specialist("prompt", lane="fast")

    # 2. Fast-lane anchor_mismatch gets 1 re-read/retry within fast budget; then static_oracle_fail escalates to full
    bad_anchor_fast = FastEditProposal(
        explanation="Wrong anchor",
        edits=[{"path": "prompcraft.py", "old_text": "import nonexistent_anchor", "new_text": "import re"}],
    )
    valid_fast = FastEditProposal(
        explanation="Replace math with re",
        edits=[{"path": "prompcraft.py", "old_text": "import math", "new_text": "import re"}],
    )
    full_prop = EditProposal(
        explanation="Replace math with re and add regression test",
        tests=[{"path": "test_prompcraft.py", "content": "from prompcraft import tags\ndef test_t():\n    assert tags('#hi') == ['hi']\n"}],
        edits=[{"path": "prompcraft.py", "old_text": "import math", "new_text": "import re"}],
    )

    fast_calls = 0
    specialist_called_in_fast = False

    class LaneCheckingRouter:
        def route_normal(self, prompt: str, **kw: Any) -> Any:
            nonlocal fast_calls, specialist_called_in_fast
            lane = kw.get("lane", "full")
            if lane == "fast":
                fast_calls += 1
                if kw.get("allow_specialist") is not False:
                    specialist_called_in_fast = True
                if kw.get("thinking_level") != "LOW":
                    raise AssertionError(f"Fast lane must use LOW thinking_level, got {kw.get('thinking_level')}")
                parsed = bad_anchor_fast if fast_calls == 1 else valid_fast
            else:
                schema = kw.get("schema")
                parsed = _approved_review() if schema is QualityReview else full_prop
            return SimpleNamespace(
                result=SimpleNamespace(parsed=parsed, raw_text=""),
                usage=SimpleNamespace(input_tokens=80, output_tokens=30),
                selected_provider="google",
                selected_model=PRIMARY,
            )

        def route_specialist(self, prompt: str, **kw: Any) -> Any:
            nonlocal specialist_called_in_fast
            if kw.get("lane") == "fast":
                specialist_called_in_fast = True
            raise AssertionError("Specialist should not be called in this flow")

    # V0 original passes (exit 0 -> oracle disagreement / static_oracle_fail) -> escalates to full lane!
    ctx = Phase3Context(
        tmp_path,
        [
            _exec_result(0, "ORACLE_V0_NO_DEFECT\n"),  # fast V0 orig disagrees -> escalates fast->full (0 attempts consumed!)
            _exec_result(0, "1 passed"),  # full baseline
            _exec_result(1, "1 failed"),  # full reproduce
            _exec_result(0, "2 passed"),  # full verify
        ],
    )
    task = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_fast_esc_no_spec",
        limits=LIMITS,
        attempts_used=0,
        issue={"title": "NameError: re not defined in prompcraft.py", "body": "Fix missing import re and unused import math in prompcraft.py"},
        plan={"files": ["prompcraft.py"]},
    )

    outcome = run_repair(task, ctx, LaneCheckingRouter())
    assert outcome.outcome == "fix_ready"
    assert fast_calls == 2  # 1 initial + 1 anchor retry within fast budget
    assert specialist_called_in_fast is False
    assert outcome.summary["lane"] == "full"
    assert ctx.attempt_count == 1  # Fast-lane escalation consumed ZERO repair attempts!


def test_fast_lane_primary_timeout_falls_back_to_36_flash_and_all_models_down_pauses_in_fast_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_ENGINE", "graph")
    monkeypatch.setenv("ADAPTIVE_LANES", "on")
    orig_code = "import math\n\ndef tags(s: str) -> list[str]:\n    return re.findall(r'#(\\w+)', s)\n"
    (tmp_path / "prompcraft.py").write_text(orig_code, encoding="utf-8")

    fast_prop = FastEditProposal(
        explanation="Replace math with re",
        edits=[{"path": "prompcraft.py", "old_text": "import math", "new_text": "import re"}],
    )
    rev = _approved_review("fix: import re in prompcraft.py")

    # Part A: Primary times out in fast lane -> falls back to gemini-3.6-flash within fast budget, while specialist breaker is OPEN!
    health = ProviderHealth()
    health.open_breaker(SPECIALIST, duration=120.0)

    class FailoverProvider(BaseProvider):
        def __init__(self) -> None:
            self.calls: list[tuple[str, float | None]] = []

        def generate(
            self,
            model_id: str,
            prompt: str,
            schema: Any = None,
            *,
            thinking_level: str | None = None,
            timeout_seconds: float | None = None,
            **kw: Any,
        ) -> ModelResult:
            self.calls.append((model_id, timeout_seconds))
            if model_id == PRIMARY:
                raise RetryableProviderError("Request timed out", "google", PRIMARY, status_code=504)
            parsed = rev if schema is QualityReview else fast_prop
            return ModelResult("google", model_id, parsed=parsed, usage=create_usage_record("google", model_id, 70, 25))

    prov_a = FailoverProvider()
    router_a = ModelRouter(
        RouterSettings(google_cloud_project="test-proj"),
        provider_factory=lambda _: prov_a,
        health=health,
    )
    ctx_a = Phase3Context(
        tmp_path,
        [
            _exec_result(1, f"{ORACLE_ORIG_MARKER}\n", exec_id="exec_v0"),
            _exec_result(0, f"ORACLE_V0_PATCH_PASS\n{ORACLE_GATE_MARKER}\n", exec_id="exec_v1"),
        ],
    )
    task_a = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_fast_failover",
        limits=LIMITS,
        attempts_used=0,
        issue={"title": "NameError: re not defined in prompcraft.py", "body": "Fix missing import re and unused import math in prompcraft.py"},
        plan={"files": ["prompcraft.py"]},
    )
    outcome_a = run_repair(task_a, ctx_a, router_a)
    assert outcome_a.outcome == "fix_ready"
    assert outcome_a.summary["lane"] == "fast"
    assert [m for m, _ in prov_a.calls] == [PRIMARY, FALLBACK, PRIMARY, FALLBACK]
    assert all(t is not None and t <= 20.0 for _, t in prov_a.calls)

    # Part B: All models down in fast lane -> pauses/requeues (infra_retry) in the SAME lane ("fast") with 0 attempts consumed!
    class OutageProvider(BaseProvider):
        def generate(self, model_id: str, prompt: str, **kw: Any) -> ModelResult:
            raise RetryableProviderError("503 Service Unavailable", "google", model_id, status_code=503)

    router_b = ModelRouter(
        RouterSettings(google_cloud_project="test-proj"),
        provider_factory=lambda _: OutageProvider(),
        health=ProviderHealth(),
    )
    ctx_b = Phase3Context(tmp_path, [])
    task_b = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_fast_outage",
        limits=LIMITS,
        attempts_used=0,
        issue={"title": "NameError: re not defined in prompcraft.py", "body": "Fix missing import re and unused import math in prompcraft.py"},
        plan={"files": ["prompcraft.py"]},
    )
    outcome_b = run_repair(task_b, ctx_b, router_b)
    assert outcome_b.outcome == "infra_retry"
    assert outcome_b.failure_code == FailureCode.LLM_UNAVAILABLE
    assert ctx_b.attempt_count == 0
    assert ctx_b.lane_states[-1]["lane"] == "fast"
    assert ctx_b.lane_states[-1]["escalations"] == []


# --- 6. Specialist Cap Survives Resume & Fast Escalation -> Argon Uses Reference Evidence ---


def test_specialist_cap_survives_resume_and_fast_escalation_to_argon_carries_evidence_by_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_ENGINE", "graph")
    monkeypatch.setenv("ADAPTIVE_LANES", "on")
    db_path = tmp_path / "phase3_ckpt.sqlite"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    orig_code = "import math\n\ndef tags(s: str) -> list[str]:\n    return re.findall(r'#(\\w+)', s)\n"
    (repo_dir / "prompcraft.py").write_text(orig_code, encoding="utf-8")

    # Fast-lane patch passes V0/V1 oracle, but fast_review rejects it -> escalates fast->full with REVIEW_REJECTED!
    # In full lane, REVIEW_REJECTED escalation triggers Argon (specialist Call #1), then crashes before `patch`!
    fast_prop = FastEditProposal(
        explanation="Replace math with re",
        edits=[{"path": "prompcraft.py", "old_text": "import math", "new_text": "import re"}],
    )
    rejected_fast_review = QualityReview(
        commit_message="fix: import re",
        branch_name="fix-re",
        approved=False,
        requirements=[{"requirement": "Needs regression test", "status": "failed", "evidence": "Missing test"}],
        blocking_issues=["Requires full regression test"],
    )
    argon_advice = SpecialistAdvice(
        root_cause_diagnosis="Fast review rejected static-only patch; add unit test in full lane.",
        rejected_hypotheses=["Static-only patch"],
        recommended_strategy="Add test_prompcraft.py and import re.",
        target_files=["prompcraft.py"],
    )
    full_prop_fail = EditProposal(
        explanation="Attempt 1 fail",
        tests=[{"path": "test_prompcraft.py", "content": "from prompcraft import tags\ndef test_t():\n    assert tags('#a') == ['a']\n"}],
        edits=[{"path": "prompcraft.py", "old_text": "import math", "new_text": "import re\n# bad"}],
    )
    full_prop_pass = EditProposal(
        explanation="Attempt 2 pass",
        tests=[{"path": "test_prompcraft.py", "content": "from prompcraft import tags\ndef test_t():\n    assert tags('#a') == ['a']\n"}],
        edits=[{"path": "prompcraft.py", "old_text": "import math", "new_text": "import re"}],
    )

    spec_prompts: list[str] = []

    class ResumeRouter:
        def __init__(self, normal_items: list[Any]) -> None:
            self.items = list(normal_items)

        def route_normal(self, prompt: str, **kw: Any) -> Any:
            item = self.items.pop(0)
            return SimpleNamespace(
                result=SimpleNamespace(parsed=item, raw_text=""),
                usage=SimpleNamespace(input_tokens=90, output_tokens=30),
                selected_provider="google",
                selected_model=PRIMARY,
            )

        def route_specialist(self, prompt: str, **kw: Any) -> Any:
            spec_prompts.append(prompt)
            return SimpleNamespace(
                result=SimpleNamespace(parsed=argon_advice, raw_text=""),
                usage=SimpleNamespace(input_tokens=150, output_tokens=50),
                selected_provider="google",
                selected_model=SPECIALIST,
            )

    task = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_spec_cap_resume",
        limits={**LIMITS, "maxSpecialistCalls": 2, "maxRepairAttempts": 4},
        attempts_used=0,
        issue={"title": "NameError: re not defined in prompcraft.py", "body": "Fix missing import re and unused import math in prompcraft.py"},
        plan={"files": ["prompcraft.py"]},
    )

    # Worker 1: fast_patch_action -> fast_static_oracle -> fast_verify -> fast_review (rejects -> escalates fast->full)
    # -> explore (baseline) -> specialist_reflect (Specialist Call #1!) -> crashes before `patch`!
    cp1 = SqliteRepairCheckpointer(db_path)
    ctx1 = Phase3Context(
        repo_dir,
        [
            _exec_result(1, f"{ORACLE_ORIG_MARKER}\n", exec_id="exec_v0"),
            _exec_result(0, f"ORACLE_V0_PATCH_PASS\n{ORACLE_GATE_MARKER}\n", exec_id="exec_v1"),
            _exec_result(0, "1 passed"),  # full baseline in explore
        ],
    )
    w1_router = ResumeRouter([fast_prop, rejected_fast_review])
    with pytest.raises(RuntimeError, match="Simulated crash before node patch"):
        run_repair_graph(task, ctx1, w1_router, checkpointer=cp1, crash_before_node="patch")

    assert len(spec_prompts) == 1
    assert "fast_to_full_review_rejected" in spec_prompts[0]
    assert "triageRef=sha256:" in spec_prompts[0]

    # Inspect persisted checkpoint: specialist_calls_used == 1, bounded size, no breaker state, compact preservedEvidence
    tid = make_thread_id(task.task_id, "execute")
    tup = cp1.get_tuple({"configurable": {"thread_id": tid}})
    assert tup is not None
    c_vals = tup.checkpoint["channel_values"]
    validate_checkpoint_payload(c_vals)
    assert c_vals["specialist_calls_used"] == 1
    assert c_vals["budgets"]["specialistCalls"] == 1
    assert len(c_vals["escalations"]) == 1
    pe = c_vals["escalations"][0]["preservedEvidence"]
    assert "fastCandidateDiffHash" in pe
    assert "fastCandidateDiff" not in pe

    # Worker 2 resumes from `patch`:
    # Attempt 1 fails verify (persistent failure #1), Attempt 2 fails verify (persistent failure #2 -> Specialist Call #2 == cap!),
    # Attempt 3 fails verify (would trigger specialist again, but cap=2 is reached so Argon is NOT called a 3rd time!),
    # Attempt 4 succeeds!
    cp2 = SqliteRepairCheckpointer(db_path)
    ctx2 = Phase3Context(
        repo_dir,
        [
            _exec_result(1, "1 failed"),  # attempt 1 reproduce
            _exec_result(1, "FAILED test_prompcraft.py::test_t - AssertionError: 1"),  # attempt 1 verify fail
            _exec_result(1, "1 failed"),  # attempt 2 reproduce
            _exec_result(1, "FAILED test_prompcraft.py::test_t - AssertionError: 2"),  # attempt 2 verify fail (triggers Spec #2)
            _exec_result(1, "1 failed"),  # attempt 3 reproduce
            _exec_result(1, "FAILED test_prompcraft.py::test_t - AssertionError: 3"),  # attempt 3 verify fail (cap=2 reached!)
            _exec_result(1, "1 failed"),  # attempt 4 reproduce
            _exec_result(0, "2 passed"),  # attempt 4 verify pass
        ],
    )
    w2_router = ResumeRouter([
        full_prop_fail,
        full_prop_fail.model_copy(update={"explanation": "Attempt 2 fail"}),
        full_prop_fail.model_copy(update={"explanation": "Attempt 3 fail"}),
        full_prop_pass,
        _approved_review("fix: import re in prompcraft.py"),
    ])
    outcome = run_repair_graph(task, ctx2, w2_router, checkpointer=cp2)
    assert outcome.outcome == "fix_ready"
    # Total specialist calls across Worker 1 + Worker 2 is capped at MAX_SPECIALIST_CALLS_PER_TASK (2), surviving resume!
    assert len(spec_prompts) == MAX_SPECIALIST_CALLS_PER_TASK


# --- 7. Best-of-N via LangGraph Send (Full Lane Only, Compact Winner in Checkpoint) ---


def test_best_of_n_send_full_lane_only_and_persists_only_chosen_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_ENGINE", "graph")
    monkeypatch.setenv("ADAPTIVE_LANES", "off")
    monkeypatch.setenv("GITRESOLVE_BEST_OF_N", "on")
    db_path = tmp_path / "best_of_n.sqlite"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "math_ops.py").write_text("def sub(a, b):\n    return a + b\n", encoding="utf-8")

    # Candidate 1 fails verification; Candidate 2 passes verification!
    cand_1_prop = EditProposal(
        explanation="Hypothesis 1: wrong operator",
        tests=[{"path": "test_math_ops.py", "content": "from math_ops import sub\ndef test_sub():\n    assert sub(5, 2) == 3\n"}],
        edits=[{"path": "math_ops.py", "old_text": "    return a + b\n", "new_text": "    return a * b\n"}],
    )
    cand_2_prop = EditProposal(
        explanation="Hypothesis 2: correct subtraction operator",
        tests=[{"path": "test_math_ops.py", "content": "from math_ops import sub\ndef test_sub():\n    assert sub(5, 2) == 3\n"}],
        edits=[{"path": "math_ops.py", "old_text": "    return a + b\n", "new_text": "    return a - b\n"}],
    )

    class BestOfNRouter:
        def route_normal(self, prompt: str, **kw: Any) -> Any:
            schema = kw.get("schema")
            if schema is QualityReview:
                parsed: Any = _approved_review("fix: subtract b from a in sub")
            elif "[cand_1]" in prompt:
                parsed = cand_1_prop
            else:
                parsed = cand_2_prop
            return SimpleNamespace(
                result=SimpleNamespace(parsed=parsed, raw_text=""),
                usage=SimpleNamespace(input_tokens=80, output_tokens=30),
                selected_provider="google",
                selected_model=PRIMARY,
            )

    cp = SqliteRepairCheckpointer(db_path)
    ctx = Phase3Context(
        repo_dir,
        [
            _exec_result(0, "1 passed"),  # baseline
            _exec_result(1, "1 failed"),  # cand_1 reproduce
            _exec_result(1, "1 failed: 10 != 3"),  # cand_1 verify FAILS
            _exec_result(1, "1 failed"),  # cand_2 reproduce
            _exec_result(0, "2 passed"),  # cand_2 verify PASSES
        ],
    )
    task = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_best_of_n",
        limits=LIMITS,
        attempts_used=0,
        issue={"title": "sub(a, b) adds instead of subtracting", "body": "Fix sub in math_ops.py"},
        plan={},
    )
    outcome = run_repair_graph(task, ctx, BestOfNRouter(), checkpointer=cp)
    assert outcome.outcome == "fix_ready"
    assert "+    return a - b" in outcome.final_diff

    # Checkpoint contains only the single chosen candidate's compact metadata
    tup = cp.get_tuple({"configurable": {"thread_id": make_thread_id(task.task_id, "execute")}})
    assert tup is not None
    c_vals = tup.checkpoint["channel_values"]
    validate_checkpoint_payload(c_vals)
    assert len(c_vals["candidate_branches"]) == 1
    assert c_vals["candidate_branches"][0]["candidate_id"] == "cand_2"
    assert c_vals["candidate_branches"][0]["selected"] is True


# --- 8. AL Eval Harness with Provider-Failure Injection & Early Specialist Flag ---


def test_al_eval_harness_with_provider_failure_injection_has_zero_unsafe_fast_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-run AL's evaluation harness with provider-failure injection.

    Reports latency per lane/model, escalation rate, specialist calls per task,
    and verifies unsafe-fast-lane count stays strictly ZERO.
    """
    monkeypatch.setenv("AGENT_ENGINE", "graph")
    monkeypatch.setenv("ADAPTIVE_LANES", "on")

    # 1. Verify node reasoning profiles respect fast vs full lane bounds
    fast_think, fast_to = resolve_node_profile("fast_patch_action", lane="fast", fast_remaining_seconds=30.0)
    assert fast_think == "LOW" and fast_to <= 18.0
    full_think, full_to = resolve_node_profile("patch", lane="full", attempt=2, task_remaining_seconds=600.0)
    assert full_think == "HIGH" and full_to == 45.0

    # 2. Run multi-scenario AL harness with provider failure injection
    harness_scenarios = [
        {
            "id": "fast_prompcraft_with_primary_timeout",
            "file": "prompcraft.py",
            "orig": "import math\n\ndef extract(s: str) -> list[str]:\n    return re.findall(r'#(\\w+)', s)\n",
            "issue": "Fix missing import re and unused import math in prompcraft.py",
            "inject_primary_timeout": True,
            "oracle_passes": True,
            "expected_final_lane": "fast",
            "full_lane_would_accept": True,
        },
        {
            "id": "fast_candidate_oracle_disagreement_escalates_to_full",
            "file": "parser.py",
            "orig": "def parse_doc(s: str):\n    return json.loads(s)\n",
            "issue": "Fix missing json import in parser.py",
            "inject_primary_timeout": False,
            "oracle_passes": False,
            "expected_final_lane": "full",
            "full_lane_would_accept": True,
        },
        {
            "id": "hard_full_lane_behavior_bug_with_early_specialist",
            "file": "pricing.py",
            "orig": "def discount(price: float, rate: float) -> float:\n    return price + (price * rate)\n",
            "issue": "discount(price, rate) in pricing.py adds discount instead of subtracting",
            "inject_primary_timeout": True,
            "oracle_passes": False,
            "early_specialist": True,
            "expected_final_lane": "full",
            "full_lane_would_accept": True,
        },
    ]

    metrics: dict[str, Any] = {
        "fast_runs": 0,
        "full_runs": 0,
        "escalations": 0,
        "specialist_calls_fast": 0,
        "specialist_calls_full": 0,
        "unsafe_fast_count": 0,
        "model_latencies_ms": {PRIMARY: [], FALLBACK: [], SPECIALIST: []},
    }

    for sc in harness_scenarios:
        if sc.get("early_specialist"):
            monkeypatch.setenv("SPECIALIST_EARLY_FOR_HARD_FULL_LANE", "on")
        else:
            monkeypatch.setenv("SPECIALIST_EARLY_FOR_HARD_FULL_LANE", "off")

        sc_dir = tmp_path / sc["id"]
        sc_dir.mkdir()
        (sc_dir / sc["file"]).write_text(sc["orig"], encoding="utf-8")

        class InjectedProvider(BaseProvider):
            def __init__(self, cfg: dict[str, Any]) -> None:
                self.cfg = cfg
                self.primary_failed_once = False

            def generate(self, model_id: str, prompt: str, schema: Any = None, **kw: Any) -> ModelResult:
                if model_id == PRIMARY and self.cfg.get("inject_primary_timeout") and not self.primary_failed_once:
                    self.primary_failed_once = True
                    raise RetryableProviderError("Injected primary 504 timeout", "google", PRIMARY, status_code=504)
                if schema is SpecialistAdvice:
                    parsed: Any = SpecialistAdvice(
                        root_cause_diagnosis="Diagnosed defect.",
                        rejected_hypotheses=[],
                        recommended_strategy="Apply minimal verified fix.",
                        target_files=[self.cfg["file"]],
                    )
                elif schema is QualityReview:
                    parsed = _approved_review(f"fix: update {self.cfg['file']}")
                elif schema is FastEditProposal:
                    if self.cfg["file"] == "prompcraft.py":
                        parsed = FastEditProposal(
                            explanation="Import re",
                            edits=[{"path": "prompcraft.py", "old_text": "import math", "new_text": "import re"}],
                        )
                    else:
                        parsed = FastEditProposal(
                            explanation="Import json",
                            edits=[{"path": "parser.py", "old_text": "def parse_doc(s: str):", "new_text": "import json\n\ndef parse_doc(s: str):"}],
                        )
                else:
                    if self.cfg["file"] == "parser.py":
                        parsed = EditProposal(
                            explanation="Import json with test",
                            tests=[{"path": "test_parser.py", "content": "from parser import parse_doc\ndef test_p():\n    assert parse_doc('[1]') == [1]\n"}],
                            edits=[{"path": "parser.py", "old_text": "def parse_doc(s: str):", "new_text": "import json\n\ndef parse_doc(s: str):"}],
                        )
                    else:
                        parsed = EditProposal(
                            explanation="Subtract discount with test",
                            tests=[{"path": "test_pricing.py", "content": "from pricing import discount\ndef test_d():\n    assert discount(100.0, 0.1) == 90.0\n"}],
                            edits=[{"path": "pricing.py", "old_text": "    return price + (price * rate)\n", "new_text": "    return price - (price * rate)\n"}],
                        )
                return ModelResult("google", model_id, parsed=parsed, usage=create_usage_record("google", model_id, 100, 35))

        router = ModelRouter(
            RouterSettings(google_cloud_project="test-proj"),
            provider_factory=lambda _, c=sc: InjectedProvider(c),
            health=ProviderHealth(),
        )
        if sc["oracle_passes"]:
            exec_seq = [
                _exec_result(1, f"{ORACLE_ORIG_MARKER}\n", exec_id="v0"),
                _exec_result(0, f"ORACLE_V0_PATCH_PASS\n{ORACLE_GATE_MARKER}\n", exec_id="v1"),
            ]
        elif sc["file"] == "parser.py":
            exec_seq = [
                _exec_result(0, "ORACLE_V0_NO_DEFECT\n"),  # oracle disagreement -> escalates to full
                _exec_result(0, "1 passed"),
                _exec_result(1, "1 failed"),
                _exec_result(0, "2 passed"),
            ]
        else:
            exec_seq = [
                _exec_result(0, "1 passed"),
                _exec_result(1, "1 failed"),
                _exec_result(0, "2 passed"),
            ]

        ctx = Phase3Context(sc_dir, exec_seq)
        task = SimpleNamespace(
            base_sha=BASE_SHA,
            task_id=f"tsk_{sc['id']}",
            limits=LIMITS,
            attempts_used=0,
            issue={"title": sc["issue"], "body": sc["issue"]},
            plan={"files": [sc["file"]]},
        )
        outcome = run_repair(task, ctx, router)
        assert outcome.outcome == "fix_ready"
        final_lane = outcome.summary["lane"]
        assert final_lane == sc["expected_final_lane"]

        if final_lane == "fast":
            metrics["fast_runs"] += 1
            if not sc["full_lane_would_accept"]:
                metrics["unsafe_fast_count"] += 1
        else:
            metrics["full_runs"] += 1
        if outcome.summary.get("escalations"):
            metrics["escalations"] += 1

        for entry in router.telemetry_log:
            m_id = entry["model"]
            if m_id in metrics["model_latencies_ms"] and entry["failureClass"] is None:
                metrics["model_latencies_ms"][m_id].append(entry["latencyMs"])
            if "argon" in m_id:
                if entry["lane"] == "fast":
                    metrics["specialist_calls_fast"] += 1
                else:
                    metrics["specialist_calls_full"] += 1

    assert metrics["unsafe_fast_count"] == 0
    assert metrics["specialist_calls_fast"] == 0
    assert metrics["specialist_calls_full"] == 1
    assert metrics["fast_runs"] == 1
    assert metrics["full_runs"] == 2
    assert metrics["escalations"] == 1
    assert len(metrics["model_latencies_ms"][FALLBACK]) >= 2
