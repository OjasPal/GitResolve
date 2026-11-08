"""Phase 4 deterministic unit, e2e & adversarial tests (0 live LLM calls).

Covers:
1. Lane policy version bump (al.v1 -> al.v2) and in-flight checkpoint migration to FULL lane with HIGH risk floor
2. Task-type triage (bug | feature | refactor | config | unclear) and monotonic LLM caution floor
3. Feature planning subgraph (spec -> acceptance_criteria -> file_plan -> await_start), plan_hash pinning, and plan-time scope/protected-path rejection
4. Mode-aware verification (bug, feature, refactor, config) in validate_fix, validate_publication, and run_repair_graph
5. Per-task scope derivation derive_limits(approved_plan, HARD_CAPS) using min() and default-deny allowed_dependency_changes in requirements*.txt
6. V2 conservative AST import-graph selective pytest for inner-loop run_tests and fallback rules (final gate stays V3)
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent.analysis_schema import IssueAnalysis
from agent.graph import build_graph, compute_plan_hash
from agent.providers import ModelResult
from app.agent.graph_engine.checkpointer import (
    CheckpointCompatibilityError,
    SqliteRepairCheckpointer,
    StoreRepairCheckpointer,
    make_thread_id,
)
from app.agent.graph_engine.engine import SpecialistAdvice, run_repair_graph
from app.agent.graph_engine.tools import RepairActionEnvelope, execute_tool_action
from app.agent.graph_engine.workspace import WorkspaceOverlay
from app.agent.lanes import (
    LANES_POLICY_VERSION,
     EscalationReason,
    LaneName,
    ORACLE_GATE_MARKER,
    ORACLE_ORIG_MARKER,
    RiskFloor,
    TaskType,
    TriageReport,
    VerificationLevel,
    classify_task_type,
    plan_verification,
    refine_task_type,
    select_tests_v2,
    triage_repository,
)
from app.agent.repair_runner import (
    EditProposal,
    FastEditProposal,
    QualityReview,
    RepairProposal,
)
from app.schemas.enums import FailureCode
from app.services.policy import (
    ALLOWED_CONFIG_PROFILES,
    HARD_CAPS,
    derive_limits,
    extract_allowed_dependency_changes,
    normalize_allowed_dependency_changes,
    validate_fix,
    validate_plan_scope,
)
from app.services.publication_patch import publication_patch, validate_publication
from sandbox.profiler import profile_repository

BASE_SHA = "d" * 40
LIMITS = {
    "maxRepairAttempts": 3,
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
    exec_id: str = "exec_p4_1",
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


def _approved_review(msg: str = "feat: implement approved change", branch: str = "implement-change") -> QualityReview:
    return QualityReview(
        commit_message=msg,
        branch_name=branch,
        approved=True,
        requirements=[
            {
                "requirement": "Requested change is implemented and verified in Docker.",
                "status": "passed",
                "evidence": "Verified in isolated sandbox.",
            }
        ],
        blocking_issues=[],
    )


class ScriptedRouter:
    def __init__(self, responses: list[Any], *, specialist_responses: list[Any] | None = None) -> None:
        self.responses = list(responses)
        self.specialist_responses = list(specialist_responses or [])
        self.prompts: list[str] = []
        self.specialist_prompts: list[str] = []

    def route_normal(self, prompt: str, *, schema: Any = None, **kwargs: Any) -> SimpleNamespace:
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError(f"ScriptedRouter exhausted at call #{len(self.prompts)} (schema={schema})")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(
            result=SimpleNamespace(
                parsed=item if not isinstance(item, str) else None,
                raw_text=item if isinstance(item, str) else "",
            ),
            usage=SimpleNamespace(input_tokens=40, output_tokens=20),
            selected_provider="google",
            selected_model="gemini-3.8-flash",
            latency_ms=12.0,
        )

    def route_specialist(self, prompt: str, *, schema: Any = None, **kwargs: Any) -> SimpleNamespace:
        self.specialist_prompts.append(prompt)
        item = (
            self.specialist_responses.pop(0)
            if self.specialist_responses
            else SpecialistAdvice(
                root_cause_diagnosis="Prior hypothesis missed edge case.",
                rejected_hypotheses=["Initial patch"],
                recommended_strategy="Revise implementation.",
                target_files=["app.py"],
            )
        )
        return SimpleNamespace(
            result=SimpleNamespace(parsed=item, raw_text=""),
            usage=SimpleNamespace(input_tokens=60, output_tokens=30),
            selected_provider="google",
            selected_model="gemini-4-argon",
            latency_ms=25.0,
        )


class Phase4Context:
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


# ==============================================================================
# 1. Lane Policy Version Bump (al.v1 -> al.v2) & In-Flight Checkpoint Migration
# ==============================================================================


def test_lanes_policy_version_bumped_to_al_v2_and_al_v1_resumes_in_full_lane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert LANES_POLICY_VERSION == "al.v2"
    monkeypatch.setenv("ADAPTIVE_LANES", "on")

    (tmp_path / "paths.py").write_text(
        "def leaf_name(raw_path: str) -> str:\n    return os.path.basename(raw_path)\n",
        encoding="utf-8",
    )
    (tmp_path / "test_paths.py").write_text(
        "from paths import leaf_name\ndef test_leaf():\n    assert leaf_name('a/b.txt') == 'b.txt'\n",
        encoding="utf-8",
    )

    ckpt_db = tmp_path / "ckpt_v1_to_v2.sqlite"
    checkpointer = SqliteRepairCheckpointer(ckpt_db)
    task = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_p4_v1_migration",
        limits=LIMITS,
        issue={"title": "NameError in paths.py", "body": "paths.py raises NameError: name 'os' is not defined in leaf_name"},
        plan={"summary": "Import os in paths.py"},
        attempts_used=0,
    )

    fast_prop = FastEditProposal(
        explanation="Add missing import os",
        edits=[
            {
                "path": "paths.py",
                "old_text": "def leaf_name(raw_path: str) -> str:\n",
                "new_text": "import os\n\n\ndef leaf_name(raw_path: str) -> str:\n",
            }
        ],
    )
    oracle_results = [
        _exec_result(1, f"{ORACLE_ORIG_MARKER}:NameError:os\n{ORACLE_GATE_MARKER}:original_failed"),
        _exec_result(0, f"{ORACLE_GATE_MARKER}:patched_passed"),
    ]
    ctx_v1 = Phase4Context(tmp_path, oracle_results)
    router_v1 = ScriptedRouter([fast_prop])

    with pytest.raises(RuntimeError, match="Simulated crash before node fast_review"):
        run_repair_graph(
            task,
            ctx_v1,
            router_v1,
            checkpointer=checkpointer,
            crash_before_node="fast_review",
            lanes_policy_version="al.v1",
        )

    # Now resume with default LANES_POLICY_VERSION ("al.v2"): must migrate to FULL lane + HIGH risk floor
    full_prop = EditProposal(
        explanation="Full lane fix after al.v1 -> al.v2 policy bump",
        tests=[
            {
                "path": "test_paths_reg.py",
                "content": "from paths import leaf_name\ndef test_leaf_reg():\n    assert leaf_name('x/y.py') == 'y.py'\n",
            }
        ],
        edits=[
            {
                "path": "paths.py",
                "old_text": "def leaf_name(raw_path: str) -> str:\n",
                "new_text": "import os\n\n\ndef leaf_name(raw_path: str) -> str:\n",
            }
        ],
    )
    ctx_v2 = Phase4Context(
        tmp_path,
        [
            _exec_result(0, "1 passed"),
            _exec_result(1, "1 failed"),
            _exec_result(0, "2 passed"),
        ],
    )
    router_v2 = ScriptedRouter([full_prop, _approved_review("fix: import os in paths.py", "fix-paths-os")])
    outcome = run_repair_graph(task, ctx_v2, router_v2, checkpointer=checkpointer)

    assert outcome.outcome == "fix_ready"
    assert outcome.summary["lane"] == "full"
    assert ctx_v2.lane_states[-1]["lane"] == "full"
    assert ctx_v2.lane_states[-1]["riskFloor"] == "high"


# ==============================================================================
# 2. Task-Type Triage & Monotonic LLM Caution Floor
# ==============================================================================


def test_task_type_triage_deterministic_and_monotonic_llm_floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADAPTIVE_LANES", "on")
    (tmp_path / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    originals = {"calc.py": (tmp_path / "calc.py").read_text(encoding="utf-8")}
    profile = profile_repository(tmp_path)

    # 1. Deterministic classifications
    c_bug, f_bug, _ = classify_task_type("Fix TypeError in calc.py when adding values", profile, originals)
    assert c_bug == TaskType.BUG and f_bug == TaskType.BUG

    c_feat, f_feat, _ = classify_task_type("Add a new CLI flag --json to export results in calc.py", profile, originals)
    assert c_feat == TaskType.FEATURE and f_feat == TaskType.FEATURE

    c_ref, f_ref, _ = classify_task_type("Refactor calc.py to extract a helper function while preserving behavior", profile, originals)
    assert c_ref == TaskType.REFACTOR and f_ref == TaskType.REFACTOR

    c_cfg, f_cfg, _ = classify_task_type("Update .flake8 max-line-length configuration", profile, originals)
    assert c_cfg == TaskType.CONFIG and f_cfg == TaskType.CONFIG

    c_unc, f_unc, _ = classify_task_type("it is broken please fix", profile, originals)
    assert c_unc == TaskType.UNCLEAR and f_unc == TaskType.UNCLEAR

    # 2. Monotonic LLM refinement: LLM can raise caution (bug -> feature/refactor/config/unclear), NEVER lower toward bug
    assert refine_task_type(TaskType.BUG, "feature") == TaskType.FEATURE
    assert refine_task_type(TaskType.BUG, "refactor") == TaskType.REFACTOR
    assert refine_task_type(TaskType.BUG, "config") == TaskType.CONFIG
    assert refine_task_type(TaskType.BUG, "unclear") == TaskType.UNCLEAR

    assert refine_task_type(TaskType.FEATURE, "bug") == TaskType.FEATURE
    assert refine_task_type(TaskType.REFACTOR, "bug") == TaskType.REFACTOR
    assert refine_task_type(TaskType.CONFIG, "bug") == TaskType.CONFIG
    assert refine_task_type(TaskType.UNCLEAR, "bug") == TaskType.UNCLEAR

    # 3. Non-bug tasks NEVER enter the fast lane even if localized to 1 file with a static diagnostic
    broken_orig = {"calc.py": "def add(a: int, b: int) -> str:\n    return os.path.basename('a')\n"}
    triage_feat = triage_repository(
        profile,
        broken_orig,
        "Add a new feature endpoint in calc.py (Trivial 1-line typo fix!)",
        {"task_type": "bug"},  # LLM attempts to downgrade feature -> bug
        set(),
    )
    assert triage_feat.task_type_candidate == TaskType.FEATURE
    assert triage_feat.task_type_floor == TaskType.FEATURE
    assert triage_feat.candidate_lane == LaneName.FULL
    assert "non_bug_task_type:feature" in triage_feat.override_reasons
    assert "untrusted_trivial_claim_ignored" in triage_feat.signals

    # 4. Backward-compatible TriageReport serialization
    legacy_dict = {
        "candidateLane": "full",
        "riskFloor": "medium",
        "localizedFiles": ["calc.py"],
        " oracleKind": None,
    }
    restored = TriageReport.from_dict(legacy_dict)
    assert restored.task_type_candidate == TaskType.BUG
    assert restored.task_type_floor == TaskType.BUG


def test_unclear_task_routes_to_needs_clarification_with_zero_attempts(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def run() -> int:\n    return 1\n", encoding="utf-8")
    task = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_p4_unclear",
        limits=LIMITS,
        issue={"title": "broken", "body": "something is wrong"},
        plan={},
        attempts_used=0,
    )
    ctx = Phase4Context(tmp_path, [])
    router = ScriptedRouter([])
    outcome = run_repair_graph(task, ctx, router)

    assert outcome.outcome == "needs_clarification"
    assert outcome.failure_code == FailureCode.ISSUE_AMBIGUOUS
    assert ctx.attempt_count == 0
    assert len(router.prompts) == 0


def test_bug_mode_with_feature_sized_diff_escalates_and_stops_for_plan_revision(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text("def compute(x: int) -> int:\n    return x\n", encoding="utf-8")
    approved_plan = {
        "task_type": "bug",
        "summary": "Fix compute off-by-one bug",
        "expected_files": ["service.py"],
        "line_budget": 120,
    }
    approved_plan["plan_hash"] = compute_plan_hash(approved_plan)

    # Proposal adds 2 new public functions (feature-sized diff in bug mode)
    feature_sized_edit = EditProposal(
        explanation="Add two new public endpoints instead of fixing bug",
        tests=[
            {
                "path": "test_service.py",
                "content": "from service import compute\ndef test_compute():\n    assert compute(1) == 2\n",
            }
        ],
        edits=[
            {
                "path": "service.py",
                "old_text": "def compute(x: int) -> int:\n    return x\n",
                "new_text": (
                    "def compute(x: int) -> int:\n    return x + 1\n\n"
                    "def export_csv(rows: list) -> str:\n    return ','.join(map(str, rows))\n\n"
                    "def export_json(rows: list) -> str:\n    return str(rows)\n"
                ),
            }
        ],
    )
    task = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_p4_bug_scope_growth",
        limits=LIMITS,
        issue={"title": "Fix compute off-by-one error in service.py", "body": "service.py compute(1) returns 1 instead of 2"},
        plan=approved_plan,
        attempts_used=0,
    )
    ctx = Phase4Context(tmp_path, [_exec_result(0, "1 passed")])
    router = ScriptedRouter([feature_sized_edit])
    outcome = run_repair_graph(task, ctx, router)

    assert outcome.outcome == "out_of_scope"
    assert outcome.failure_code == FailureCode.POLICY_VIOLATION
    assert "feature-sized" in (outcome.failure_message or "").lower()


# ==============================================================================
# 3. Feature Subgraph, Plan Hash Pinning, and Plan-Time Scope/Protected Rejection
# ==============================================================================


def test_feature_planning_subgraph_populates_structured_plan_and_pins_hash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    analysis = IssueAnalysis(
        task_title="Add markdown summary formatter in formatter.py",
        problem_summary="Add a markdown summary formatter in formatter.py",
        investigation_steps=[
            "Inspect formatter.py exports",
            "Implement format_summary(items) returning a markdown bullet list",
            "Add acceptance tests for format_summary",
        ],
        needs_more_information=False,
        questions_for_reporter=[],
        task_type="feature",
        spec="Implement format_summary(items: list[str]) -> str in formatter.py.",
        acceptance_criteria=[
            "format_summary(['a', 'b']) returns '- a\\n- b'",
            "Empty list returns ''",
        ],
        expected_files=["formatter.py"],
        line_budget=45,
        dependency_changes=[],
    )
    monkeypatch.setattr("agent.graph.analyze_issue_with_gemini", lambda **kw: analysis)

    graph = build_graph()
    state = graph.invoke(
        {
            "task_id": "tsk_plan_feat",
            "repo_full_name": "acme/demo",
            "issue_number": 42,
            "issue_title": "Add markdown summary formatter",
            "issue_body": "Implement a new public function format_summary in formatter.py",
            "stage": "receive",
            "status": "queued",
            "plan": [],
            "task_title": "",
            "problem_summary": "",
            "needs_more_information": False,
            "questions_for_reporter": [],
            "task_type": "bug",
            "spec": "",
            "acceptance_criteria": [],
            "expected_files": [],
            "line_budget": 150,
            "dependency_changes": [],
            "plan_hash": "",
            "error": None,
            "router_metadata": None,
        }
    )

    assert state["status"] == "awaiting_start"
    assert state["task_type"] == "feature"
    assert state["spec"] == "Implement format_summary(items: list[str]) -> str in formatter.py."
    assert len(state["acceptance_criteria"]) == 2
    assert state["expected_files"] == ["formatter.py"]
    assert state["line_budget"] == 45
    assert state["plan_hash"].startswith("sha256:")

    # Verify checkpointer pins plan_hash and rejects tampering across resume
    ckpt = SqliteRepairCheckpointer(tmp_path / "plan_pin.sqlite")
    tid = make_thread_id("tsk_plan_feat", "execute")
    ckpt.pin_or_verify_thread(
        tid,
        engine_mode="langgraph",
        base_sha=BASE_SHA,
        lanes_policy_version=LANES_POLICY_VERSION,
        adaptive_lanes_mode="on",
        pinned_plan_hash=state["plan_hash"],
    )
    with pytest.raises(CheckpointCompatibilityError, match="Approved plan hash mismatch"):
        ckpt.pin_or_verify_thread(
            tid,
            engine_mode="langgraph",
            base_sha=BASE_SHA,
            lanes_policy_version=LANES_POLICY_VERSION,
            adaptive_lanes_mode="on",
            pinned_plan_hash="0" * 64,
        )


@pytest.mark.parametrize(
    "bad_plan,expected_match",
    [
        (
            {"expected_files": ["a.py", "b.py", "c.py", "d.py", "e.py", "f.py"], "line_budget": 80},
            "exceeds hard cap",
        ),
        (
            {"expected_files": ["a.py"], "line_budget": 200},
            "exceeds hard cap",
        ),
        (
            {"expected_files": ["pyproject.toml"], "line_budget": 20},
            "protected or unsupported file",
        ),
        (
            {"expected_files": [".github/workflows/ci.yml"], "line_budget": 20},
            "protected or unsupported file",
        ),
        (
            {"expected_files": ["Dockerfile"], "line_budget": 20},
            "protected or unsupported file",
        ),
    ],
)
def test_plan_exceeding_hard_caps_or_listing_protected_files_rejected_at_plan_time(
    bad_plan: dict[str, Any], expected_match: str
) -> None:
    with pytest.raises(ValueError, match=expected_match):
        validate_plan_scope(bad_plan, LIMITS)


# ==============================================================================
# 4. Mode-Aware Verification (Feature, Refactor, Config) & Static-Oracle Guard
# ==============================================================================


@pytest.mark.parametrize("non_bug_mode", ["feature", "refactor", "config"])
def test_static_oracle_strictly_rejected_for_non_bug_modes(non_bug_mode: str) -> None:
    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,2 +1,3 @@\n"
        "+import os\n"
        " def f():\n"
        "     return 1\n"
    )
    static_oracle_verification = {
        "reproduced": True,
        "regressionPassed": True,
        "testsRun": 0,
        "newFailures": [],
        "lane": "fast",
        "verificationLevel": "V1",
        "oracle": {"originalFailed": True, "patchedPassed": True, "validForFastLane": True},
        "taskType": non_bug_mode,
        "baselinePassed": True,
        "touchedSymbolsCovered": True,
        "configProfileValidated": True,
    }
    with pytest.raises(ValueError):
        validate_fix(
            diff,
            LIMITS,
            static_oracle_verification,
            BASE_SHA,
            require_regression=False,
            allow_static_oracle=True,  # Even if caller passes True, validate_fix forces False for non-bug modes
            task_type=non_bug_mode,
        )


def test_feature_mode_e2e_strips_sandbox_acceptance_test_and_verifies_full_suite(tmp_path: Path) -> None:
    (tmp_path / "formatter.py").write_text(
        "def format_title(text: str) -> str:\n    return text.strip().title()\n",
        encoding="utf-8",
    )
    (tmp_path / "test_formatter.py").write_text(
        "from formatter import format_title\ndef test_title():\n    assert format_title(' hello ') == 'Hello'\n",
        encoding="utf-8",
    )
    approved_plan = {
        "task_type": "feature",
        "spec": "Add format_bullets(items: list[str]) -> str to formatter.py",
        "acceptance_criteria": ["Returns markdown bullet lines prefixed with '- '"],
        "expected_files": ["formatter.py"],
        "line_budget": 50,
        "dependency_changes": [],
    }
    approved_plan["plan_hash"] = compute_plan_hash(approved_plan)

    feat_proposal = EditProposal(
        explanation="Implement format_bullets according to approved feature spec",
        tests=[
            {
                "path": "test_feature_bullets.py",
                "content": (
                    "import formatter\n"
                    "def test_format_bullets():\n"
                    "    assert hasattr(formatter, 'format_bullets')\n"
                    "    assert formatter.format_bullets(['a', 'b']) == '- a\\n- b'\n"
                ),
            }
        ],
        edits=[
            {
                "path": "formatter.py",
                "old_text": "def format_title(text: str) -> str:\n    return text.strip().title()\n",
                "new_text": (
                    "def format_title(text: str) -> str:\n    return text.strip().title()\n\n\n"
                    "def format_bullets(items: list[str]) -> str:\n"
                    "    return '\\n'.join(f'- {item}' for item in items)\n"
                ),
            }
        ],
    )
    task = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_p4_feature_e2e",
        limits=LIMITS,
        issue={"title": "Feature: add format_bullets helper", "body": "Implement new function format_bullets in formatter.py"},
        plan=approved_plan,
        attempts_used=0,
    )
    ctx = Phase4Context(
        tmp_path,
        [
            _exec_result(0, "1 passed"),  # baseline
            _exec_result(1, "1 failed"),  # reproduce (acceptance test fails on original)
            _exec_result(0, "2 passed"),  # verify (full suite passes with feature)
        ],
    )
    router = ScriptedRouter([feat_proposal, _approved_review("feat: add format_bullets to formatter", "feat-format-bullets")])
    outcome = run_repair_graph(task, ctx, router)

    assert outcome.outcome == "fix_ready"
    assert "def format_bullets" in outcome.final_diff
    assert "test_feature_bullets.py" not in outcome.final_diff
    assert outcome.verification["taskType"] == "feature"
    assert outcome.verification["reproduced"] is True
    assert outcome.verification["regressionPassed"] is True
    assert outcome.verification["testsRun"] == 2
    assert outcome.summary["planRef"]["planHash"] == approved_plan["plan_hash"]


def test_refactor_mode_e2e_verifies_pass_before_and_after_and_symbol_coverage(tmp_path: Path) -> None:
    (tmp_path / "pricing.py").write_text(
        "def compute_total(prices: list[float], tax_rate: float) -> float:\n"
        "    subtotal = 0.0\n"
        "    for p in prices:\n"
        "        subtotal += p\n"
        "    return round(subtotal * (1.0 + tax_rate), 2)\n",
        encoding="utf-8",
    )
    (tmp_path / "test_pricing.py").write_text(
        "from pricing import compute_total\n"
        "def test_compute_total():\n"
        "    assert compute_total([10.0, 20.0], 0.1) == 33.0\n",
        encoding="utf-8",
    )
    approved_plan = {
        "task_type": "refactor",
        "spec": "Refactor compute_total in pricing.py to use sum() while preserving behavior.",
        "acceptance_criteria": ["compute_total signature and behavior remain unchanged"],
        "expected_files": ["pricing.py"],
        "line_budget": 30,
        "dependency_changes": [],
    }
    approved_plan["plan_hash"] = compute_plan_hash(approved_plan)

    refactor_proposal = EditProposal(
        explanation="Simplify subtotal accumulation with built-in sum()",
        tests=[
            {
                "path": "test_pricing_char.py",
                "content": (
                    "from pricing import compute_total\n"
                    "def test_compute_total_empty_and_rounding():\n"
                    "    assert compute_total([], 0.15) == 0.0\n"
                    "    assert compute_total([1.25, 2.75], 0.0) == 4.0\n"
                ),
            }
        ],
        edits=[
            {
                "path": "pricing.py",
                "old_text": (
                    "    subtotal = 0.0\n"
                    "    for p in prices:\n"
                    "        subtotal += p\n"
                ),
                "new_text": "    subtotal = float(sum(prices))\n",
            }
        ],
    )
    task = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_p4_refactor_e2e",
        limits=LIMITS,
        issue={"title": "Refactor compute_total in pricing.py", "body": "Refactor compute_total to deduplicate loop while preserving behavior"},
        plan=approved_plan,
        attempts_used=0,
    )
    ctx = Phase4Context(
        tmp_path,
        [
            _exec_result(0, "1 passed"),  # baseline passes BEFORE
            _exec_result(0, "1 passed"),  # characterization test passes on ORIGINAL
            _exec_result(0, "2 passed"),  # full suite passes AFTER
        ],
    )
    router = ScriptedRouter([refactor_proposal, _approved_review("refactor: simplify compute_total with sum", "refactor-pricing")])
    outcome = run_repair_graph(task, ctx, router)

    assert outcome.outcome == "fix_ready"
    assert "float(sum(prices))" in outcome.final_diff
    assert "test_pricing_char.py" not in outcome.final_diff
    assert outcome.verification["taskType"] == "refactor"
    assert outcome.verification["baselinePassed"] is True
    assert outcome.verification["touchedSymbolsCovered"] is True
    assert outcome.verification["unapprovedExportChanges"] == []


def test_refactor_mode_uncovered_symbols_returns_transparent_needs_clarification(tmp_path: Path) -> None:
    (tmp_path / "legacy.py").write_text(
        "def untested_transform(data: list[int]) -> list[int]:\n"
        "    out = []\n"
        "    for x in data:\n"
        "        out.append(x * 2)\n"
        "    return out\n",
        encoding="utf-8",
    )
    approved_plan = {
        "task_type": "refactor",
        "spec": "Refactor untested_transform in legacy.py",
        "acceptance_criteria": ["Preserve behavior of untested_transform"],
        "expected_files": ["legacy.py"],
        "line_budget": 30,
    }
    # Model proposes refactoring untested_transform WITHOUT any characterization test covering it
    no_test_edit = EditProposal(
        explanation="Refactor untested_transform to list comprehension without tests",
        tests=[],
        edits=[
            {
                "path": "legacy.py",
                "old_text": (
                    "    out = []\n"
                    "    for x in data:\n"
                    "        out.append(x * 2)\n"
                    "    return out\n"
                ),
                "new_text": "    return [x * 2 for x in data]\n",
            }
        ],
    )
    task = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_p4_refactor_uncovered",
        limits={**LIMITS, "maxRepairAttempts": 1},
        issue={"title": "Refactor untested_transform in legacy.py", "body": "Refactor untested_transform to list comprehension"},
        plan=approved_plan,
        attempts_used=0,
    )
    ctx = Phase4Context(tmp_path, [_exec_result(0, "1 passed")])
    router = ScriptedRouter([no_test_edit])
    outcome = run_repair_graph(task, ctx, router)

    assert outcome.outcome == "needs_clarification"
    assert outcome.failure_code == FailureCode.NOT_REPRODUCED
    assert "untested_transform" in (outcome.failure_message or "")


def test_refactor_mode_rejects_unapproved_public_export_removal(tmp_path: Path) -> None:
    (tmp_path / "api.py").write_text(
        "def public_handler(val: int) -> int:\n    return val + 1\n",
        encoding="utf-8",
    )
    (tmp_path / "test_api.py").write_text(
        "from api import _internal_handler\ndef test_h():\n    assert True\n",
        encoding="utf-8",
    )
    approved_plan = {
        "task_type": "refactor",
        "spec": "Refactor internal implementation of public_handler in api.py",
        "acceptance_criteria": ["Keep public_handler API intact"],
        "expected_files": ["api.py"],
        "line_budget": 30,
    }
    rename_export_edit = EditProposal(
        explanation="Rename public_handler to _internal_handler",
        tests=[],
        edits=[
            {
                "path": "api.py",
                "old_text": "def public_handler(val: int) -> int:\n",
                "new_text": "def _internal_handler(val: int) -> int:\n",
            }
        ],
    )
    task = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_p4_refactor_export",
        limits={**LIMITS, "maxRepairAttempts": 1},
        issue={"title": "Refactor public_handler in api.py", "body": "Refactor public_handler in api.py"},
        plan=approved_plan,
        attempts_used=0,
    )
    ctx = Phase4Context(tmp_path, [_exec_result(0, "1 passed")])
    router = ScriptedRouter([rename_export_edit])
    outcome = run_repair_graph(task, ctx, router)

    assert outcome.outcome == "out_of_scope"
    assert outcome.failure_code == FailureCode.POLICY_VIOLATION
    assert "public_handler" in (outcome.failure_message or "")


def test_config_mode_e2e_validates_allowlisted_profile_and_rejects_non_allowlisted_files(tmp_path: Path) -> None:
    (tmp_path / ".flake8").write_text("[flake8]\nmax-line-length = 88\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("def ok() -> int:\n    return 1\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text("from app import ok\ndef test_ok():\n    assert ok() == 1\n", encoding="utf-8")

    approved_plan = {
        "task_type": "config",
        "spec": "Update .flake8 max-line-length to 100",
        "acceptance_criteria": [".flake8 sets max-line-length = 100"],
        "expected_files": [".flake8"],
        "line_budget": 10,
    }
    cfg_edit = EditProposal(
        explanation="Update max-line-length in .flake8",
        tests=[],
        edits=[
            {
                "path": ".flake8",
                "old_text": "max-line-length = 88\n",
                "new_text": "max-line-length = 100\n",
            }
        ],
    )
    task = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_p4_config_e2e",
        limits=LIMITS,
        issue={"title": "Config: update .flake8 max-line-length", "body": "Update .flake8 max-line-length to 100"},
        plan=approved_plan,
        attempts_used=0,
    )
    ctx = Phase4Context(
        tmp_path,
        [
            _exec_result(0, "1 passed"),  # baseline
            _exec_result(0, "1 passed"),  # verify
        ],
    )
    router = ScriptedRouter([cfg_edit, _approved_review("chore(config): set flake8 max-line-length to 100", "config-flake-eight")])
    outcome = run_repair_graph(task, ctx, router)

    assert outcome.outcome == "fix_ready"
    assert "max-line-length = 100" in outcome.final_diff
    assert outcome.verification["taskType"] == "config"
    assert outcome.verification["configProfileValidated"] is True

    # Negative check: config mode touching Python source file is rejected as out_of_scope
    bad_cfg_edit = EditProposal(
        explanation="Modify app.py in config mode",
        tests=[],
        edits=[
            {
                "path": "app.py",
                "old_text": "    return 1\n",
                "new_text": "    return 2\n",
            }
        ],
    )
    ctx_bad = Phase4Context(tmp_path, [_exec_result(0, "1 passed")])
    router_bad = ScriptedRouter([bad_cfg_edit])
    outcome_bad = run_repair_graph(task, ctx_bad, router_bad)
    assert outcome_bad.outcome == "out_of_scope"
    assert outcome_bad.failure_code == FailureCode.POLICY_VIOLATION


# ==============================================================================
# 5. Scope Derivation (derive_limits with min()) & Per-Task Dependency Permissions
# ==============================================================================


def test_derive_limits_uses_min_and_enforces_narrower_plan_scope(tmp_path: Path) -> None:
    narrow_plan = {
        "task_type": "bug",
        "expected_files": ["mod_a.py"],
        "line_budget": 15,
    }
    derived = derive_limits(narrow_plan, HARD_CAPS)
    assert derived["maxDiffFiles"] == 1
    assert derived["maxDiffLines"] == 15

    # Even if plan asks for more than HARD_CAPS, min() clamps to HARD_CAPS
    huge_plan = {
        "task_type": "feature",
        "expected_files": [f"f{i}.py" for i in range(10)],
        "line_budget": 999,
    }
    clamped = derive_limits(huge_plan, HARD_CAPS)
    assert clamped["maxDiffFiles"] == HARD_CAPS["maxDiffFiles"]
    assert clamped["maxDiffLines"] == HARD_CAPS["maxDiffLines"]

    # Patch touching 2 implementation files when plan derived maxDiffFiles=1 fails with out_of_scope
    (tmp_path / "mod_a.py").write_text("def a() -> int:\n    return 1\n", encoding="utf-8")
    (tmp_path / "mod_b.py").write_text("def b() -> int:\n    return 2\n", encoding="utf-8")
    two_file_edit = EditProposal(
        explanation="Touch both mod_a.py and mod_b.py",
        tests=[
            {
                "path": "test_mods.py",
                "content": "from mod_a import a\ndef test_a():\n    assert a() == 10\n",
            }
        ],
        edits=[
            {"path": "mod_a.py", "old_text": "    return 1\n", "new_text": "    return 10\n"},
            {"path": "mod_b.py", "old_text": "    return 2\n", "new_text": "    return 20\n"},
        ],
    )
    task = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_p4_scope_growth",
        limits=LIMITS,
        issue={"title": "Fix a() in mod_a.py", "body": "mod_a.py a() should return 10"},
        plan=narrow_plan,
        attempts_used=0,
    )
    ctx = Phase4Context(tmp_path, [_exec_result(0, "1 passed")])
    router = ScriptedRouter([two_file_edit])
    outcome = run_repair_graph(task, ctx, router)

    assert outcome.outcome == "out_of_scope"
    assert outcome.failure_code == FailureCode.POLICY_VIOLATION


def test_dependency_permissions_default_deny_and_approved_exact_pin_rebuilds_env(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("pytest==8.2.0\n", encoding="utf-8")
    (tmp_path / "exporter.py").write_text("def send() -> int:\n    return 200\n", encoding="utf-8")
    (tmp_path / "test_exporter.py").write_text("from exporter import send\ndef test_send():\n    assert send() == 200\n", encoding="utf-8")

    req_And_code_edit = EditProposal(
        explanation="Add requests==2.32.3 to requirements.txt and use it in exporter.py",
        tests=[
            {
                "path": "test_exporter_dep.py",
                "content": "from exporter import send\ndef test_send_dep():\n    assert send() == 201\n",
            }
        ],
        edits=[
            {
                "path": "requirements.txt",
                "old_text": "pytest==8.2.0\n",
                "new_text": "pytest==8.2.0\nrequests==2.32.3\n",
            },
            {
                "path": "exporter.py",
                "old_text": "    return 200\n",
                "new_text": "    return 201\n",
            },
        ],
    )

    # 1. Default deny: without dependency_changes in approved plan, editing requirements.txt is rejected
    task_unapproved = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_p4_dep_deny",
        limits=LIMITS,
        issue={"title": "Update exporter", "body": "Update exporter response code"},
        plan={"task_type": "feature", "expected_files": ["exporter.py"], "line_budget": 40, "dependency_changes": []},
        attempts_used=0,
    )
    ctx_deny = Phase4Context(tmp_path, [_exec_result(0, "1 passed")])
    router_deny = ScriptedRouter([req_And_code_edit])
    outcome_deny = run_repair_graph(task_unapproved, ctx_deny, router_deny)
    assert outcome_deny.outcome == "out_of_scope"
    assert outcome_deny.failure_code == FailureCode.POLICY_VIOLATION

    # 2. Approved exact pin: rebuilds environment via prepare_environment and succeeds in FULL lane + V3
    approved_dep_plan = {
        "task_type": "feature",
        "spec": "Add requests==2.32.3 and update exporter.py",
        "acceptance_criteria": ["send() returns 201"],
        "expected_files": ["requirements.txt", "exporter.py"],
        "line_budget": 40,
        "dependency_changes": [
            {"package": "requests==2.32.3", "reason": "HTTP client for webhook exporter"}
        ],
    }
    approved_dep_plan["plan_hash"] = compute_plan_hash(approved_dep_plan)
    task_approved = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_p4_dep_allow",
        limits=LIMITS,
        issue={"title": "Feature: add requests to exporter", "body": "Add requests==2.32.3 and update exporter.py"},
        plan=approved_dep_plan,
        attempts_used=0,
    )
    ctx_allow = Phase4Context(
        tmp_path,
        [
            _exec_result(0, "1 passed"),  # baseline
            _exec_result(1, "1 failed"),  # reproduce
            _exec_result(0, "2 passed"),  # verify after env rebuild
        ],
    )
    router_allow = ScriptedRouter([req_And_code_edit, _approved_review("feat: add requests dependency", "feat-requests-dep")])
    outcome_allow = run_repair_graph(task_approved, ctx_allow, router_allow)

    assert outcome_allow.outcome == "fix_ready"
    assert "requests==2.32.3" in outcome_allow.final_diff
    assert "test_exporter_dep.py" not in outcome_allow.final_diff
    assert outcome_allow.verification["environmentRebuilt"] is True
    assert outcome_allow.verification["approvedDependencyChanges"] == ["requests==2.32.3"]
    assert any("requests==2.32.3" in prep for prep in ctx_allow.env_preparations)


@pytest.mark.parametrize(
    "bad_dep_spec",
    [
        [{"package": "requests>=2.31.0", "reason": "unpinned range"}],
        [{"package": "git+https://github.com/psf/requests.git", "reason": "vcs url"}],
        [{"package": "--extra-index-url https://evil.example/simple", "reason": "pip option"}],
        [{"package": "reqeusts==2.31.0", "reason": "typosquat package"}],
        [{"package": "requests==2.32.3", "reason": ""}],
    ],
)
def test_dependency_permission_rejects_unpinned_urls_options_and_typosquats(bad_dep_spec: list[dict[str, str]]) -> None:
    with pytest.raises(ValueError):
        normalize_allowed_dependency_changes(bad_dep_spec)


def test_dependency_permission_rejects_unapproved_removal_in_requirements_diff() -> None:
    removal_diff = (
        "diff --git a/requirements.txt b/requirements.txt\n"
        "--- a/requirements.txt\n"
        "+++ b/requirements.txt\n"
        "@@ -1,2 +1,2 @@\n"
        "-pytest==8.2.0\n"
        "+requests==2.32.3\n"
    )
    with pytest.raises(ValueError, match="Unapproved dependency removal"):
        validate_fix(
            removal_diff,
            LIMITS,
            {"reproduced": True, "regressionPassed": True, "testsRun": 2, "newFailures": []},
            BASE_SHA,
            require_regression=False,
            task_type="feature",
            allowed_dependency_changes=("requests==2.32.3",),
        )


# ==============================================================================
# 6. V2 Conservative AST Import-Graph Selective Pytest & Fallback Rules
# ==============================================================================


def test_v2_ast_import_graph_selects_transitive_tests_and_falls_back_conservatively() -> None:
    originals = {
        "pkg/__init__.py": "",
        "pkg/core.py": "def base_calc(x: int) -> int:\n    return x + 1\n",
        "pkg/service.py": "from pkg.core import base_calc\ndef run_service(x: int) -> int:\n    return base_calc(x) * 2\n",
        "pkg/unrelated.py": "def other() -> str:\n    return 'ok'\n",
        "tests/test_service.py": "from pkg.service import run_service\ndef test_svc():\n    assert run_service(2) == 6\n",
        "tests/test_unrelated.py": "from pkg.unrelated import other\ndef test_other():\n    assert other() == 'ok'\n",
    }

    # 1. Editing pkg/core.py transitively selects tests/test_service.py and skips tests/test_unrelated.py
    res = select_tests_v2(originals, ["pkg/core.py"])
    assert res.used_fallback is False
    assert res.selected_tests == ("tests/test_service.py",)
    assert res.total_test_files == 2
    assert res.saved_tests == 1

    # 2. Dynamic import in repository triggers conservative full-suite fallback
    dyn_originals = {
        **originals,
        "pkg/core.py": "import importlib\ndef base_calc(x: int) -> int:\n    importlib.import_module('math')\n    return x\n",
    }
    res_dyn = select_tests_v2(dyn_originals, ["pkg/core.py"])
    assert res_dyn.used_fallback is True
    assert "dynamic_import" in res_dyn.fallback_reason

    # 3. Editing conftest.py triggers full-suite fallback
    conf_originals = {**originals, "tests/conftest.py": "import pytest\n"}
    res_conf = select_tests_v2(conf_originals, ["tests/conftest.py"])
    assert res_conf.used_fallback is True
    assert "conftest_modified" in res_conf.fallback_reason

    # 4. Non-Python file edit triggers full-suite fallback
    res_non_py = select_tests_v2(originals, [".flake8"])
    assert res_non_py.used_fallback is True
    assert "non_python_edit" in res_non_py.fallback_reason


def test_inner_loop_run_tests_uses_v2_selection_while_final_verify_runs_full_suite_v3(tmp_path: Path) -> None:
    (tmp_path / "core.py").write_text("def inc(x: int) -> int:\n    return x - 1\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("def noop() -> int:\n    return 0\n", encoding="utf-8")
    (tmp_path / "test_core.py").write_text("from core import inc\ndef test_inc():\n    assert inc(1) == 2\n", encoding="utf-8")
    (tmp_path / "test_other.py").write_text("from other import noop\ndef test_noop():\n    assert noop() == 0\n", encoding="utf-8")

    originals = {
        p.name: p.read_text(encoding="utf-8")
        for p in tmp_path.glob("*.py")
    }
    overlay = WorkspaceOverlay(base_sha=BASE_SHA, originals=originals)
    overlay.apply_edit("core.py", "    return x - 1\n", "    return x + 1\n")

    ctx = Phase4Context(tmp_path, [_exec_result(0, "1 passed")])
    budgets: dict[str, int] = {"toolCalls": 0}
    tool_res = execute_tool_action(
        RepairActionEnvelope(action="run_tests", args={}, summary="Run inner-loop selective pytest"),
        overlay=overlay,
        ctx=ctx,
        base_sha=BASE_SHA,
        default_test_command=["python", "-m", "pytest", "-q", "--tb=short"],
        attempt=1,
        budgets=budgets,
    )

    assert tool_res.ok is True
    assert tool_res.extra["v2Selection"]["usedFallback"] is False
    assert tool_res.extra["v2Selection"]["selectedTests"] == ["test_core.py"]
    assert tool_res.extra["v2Selection"]["savedTests"] == 1
    assert ctx.commands[-1]["command"] == ["python", "-m", "pytest", "-q", "--tb=short", "--", "test_core.py"]
