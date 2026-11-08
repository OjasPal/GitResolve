"""Comprehensive unit and shadow-evaluation tests for Adaptive Lanes (AL)."""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
import pytest

from app.agent.lanes import (
    ChangeClass,
    EscalationReason,
    FAST_MAX_PROVIDER_CALLS,
    FAST_SUB_DEADLINE_SECONDS,
    LaneName,
    LaneRuntimeState,
    ORACLE_GATE_MARKER,
    ORACLE_ORIG_MARKER,
    RiskFloor,
    VerificationLevel,
    check_fast_budget,
    escalate,
    plan_verification,
    record_fast_usage,
    triage_repository,
)
from app.agent.repair_runner import (
    EditProposal,
    FastEditProposal,
    QualityReview,
    RepairProposal,
    run_repair,
)
from app.services.ids import sha256_hash
from app.services.publication_patch import validate_publication
from sandbox.profiler import profile_repository

LIMITS = {"maxRepairAttempts": 2, "maxDiffFiles": 5, "maxDiffLines": 150}
BASE_SHA = "b" * 40


def _approved_review(msg: str = "fix: Resolve missing import"):
    return QualityReview(
        commit_message=msg,
        branch_name="fix-missing-import",
        approved=True,
        requirements=[{"requirement": "Import resolved", "status": "passed", "evidence": "V0/V1 oracle passed in Docker."}],
        blocking_issues=[],
    )


def _exec_result(code: int, stdout: str, exec_id: str = "exec_docker_1"):
    return SimpleNamespace(
        status="passed" if code == 0 else "failed",
        exit_code=code,
        stdout=stdout,
        stderr="",
        oom_killed=False,
        cleanup_ok=True,
        execution_id=exec_id,
    )


class ScriptedContext:
    def __init__(self, root, results):
        self.root = root
        self.results = iter(results)
        self.events = []
        self.stages = []
        self.commands = []
        self.artifacts = {}
        self.attempt_count = 0
        self.budgets = {"modelCalls": 0, "inputTokens": 0, "outputTokens": 0}
        self._svc = SimpleNamespace(store=SimpleNamespace(get=lambda path: {"budgets": self.budgets.copy()}))

    def checkpoint(self):
        pass

    def usage(self, attempts_used=None, **kw):
        self.budgets.update(kw)
        if attempts_used is not None:
            self.attempt_count = attempts_used

    def stage(self, stage, **kw):
        self.stages.append(stage)

    def event(self, kind, message, **kw):
        self.events.append((kind, message))

    def artifact(self, name, kind, content, *args):
        self.artifacts[name] = content

    @contextmanager
    def repository(self):
        yield self.root

    def execute(self, **kw):
        self.commands.append(kw)
        return next(self.results)


# --- 1. Hard Override Tests (each override forces the full lane) ---


@pytest.mark.parametrize(
    "rel_path,orig_code,patch_code,expected_reason",
    [
        (
            "src/auth.py",
            "def verify_token(t):\n    return re.match(r'^ok', t)\n",
            "import re\n\ndef verify_token(t):\n    return re.match(r'^ok', t)\n",
            "auth_security_crypto",
        ),
        (
            "src/serializer.py",
            "def load_data(b):\n    return pickle.loads(b)\n",
            "import pickle\n\ndef load_data(b):\n    return pickle.loads(b)\n",
            "serialization",
        ),
        (
            "src/runner.py",
            "def run_cmd(c):\n    return subprocess.run(c)\n",
            "import subprocess\n\ndef run_cmd(c):\n    return subprocess.run(c)\n",
            "subprocess_eval",
        ),
        (
            "src/dynamic.py",
            "def calc(expr):\n    return eval(expr)\n",
            "import math\n\ndef calc(expr):\n    return eval(expr)\n",
            "subprocess_eval",
        ),
        (
            "src/queries.py",
            "def fetch(db):\n    return db.execute('SELECT * FROM users')\n",
            "import os\n\ndef fetch(db):\n    return db.execute('SELECT * FROM users')\n",
            "sql_execution",
        ),
        (
            ".github/workflows/ci.yml",
            "name: ci\n",
            "name: ci-updated\n",
            "ci_docker_dependency_or_config_path",
        ),
        (
            "Dockerfile",
            "FROM python:3.12\n",
            "FROM python:3.12-slim\n",
            "ci_docker_dependency_or_config_path",
        ),
        (
            "requirements.txt",
            "pytest==8.0.0\n",
            "pytest==8.1.0\n",
            "ci_docker_dependency_or_config_path",
        ),
        (
            "uv.lock",
            "version = 1\n",
            "version = 2\n",
            "ci_docker_dependency_or_config_path",
        ),
        (
            "src/migrations/0001_initial.py",
            "def upgrade():\n    return Path('.')\n",
            "from pathlib import Path\n\ndef upgrade():\n    return Path('.')\n",
            "migration_path",
        ),
        (
            "src/__init__.py",
            "from pathlib import Path\n",
            "import os\nfrom pathlib import Path\n",
            "public_api_or_init_export",
        ),
        (
            "tests/test_app.py",
            "def test_ok():\n    assert Path('.')\n",
            "from pathlib import Path\n\ndef test_ok():\n    assert Path('.')\n",
            "test_or_conftest_edit",
        ),
        (
            "conftest.py",
            "def fixture_a():\n    return Path('.')\n",
            "from pathlib import Path\n\ndef fixture_a():\n    return Path('.')\n",
            "test_or_conftest_edit",
        ),
        (
            "src/config_loader.py",
            "api_key = 'sk-1234567890abcdefghijklmnop'\ndef f():\n    return Path('.')\n",
            "from pathlib import Path\napi_key = 'sk-1234567890abcdefghijklmnop'\ndef f():\n    return Path('.')\n",
            "secrets_like_content",
        ),
        (
            "src/fetcher.py",
            "def get_json():\n    return requests.get('https://example.com')\n",
            "import requests\n\ndef get_json():\n    return requests.get('https://example.com')\n",
            "new_third_party_dependency",
        ),
        (
            "src/side_effect.py",
            "import atexit\n\ndef clean(text):\n    return re.sub(r'\\s+', ' ', text)\n",
            "import re\n\ndef clean(text):\n    return re.sub(r'\\s+', ' ', text)\n",
            "possible_side_effect_removal",
        ),
        (
            "src/referenced_removal.py",
            "import os\n\ndef cur():\n    return os.getcwd()\n",
            "import sys\n\ndef cur():\n    return os.getcwd()\n",
            "referenced_removal",
        ),
    ],
)
def test_each_hard_override_forces_full_lane(tmp_path, rel_path, orig_code, patch_code, expected_reason):
    file_path = tmp_path / rel_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(orig_code, encoding="utf-8")
    (tmp_path / "main.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    profile = profile_repository(tmp_path)
    originals = {rel_path: orig_code, "main.py": "def ok():\n    return 1\n"}
    patched = {rel_path: patch_code, "main.py": "def ok():\n    return 1\n"}
    triage = triage_repository(profile, originals, f"Fix {rel_path}")
    plan = plan_verification(originals, patched, triage)
    assert plan.final_lane == LaneName.FULL
    assert plan.verification_level == VerificationLevel.V3
    assert expected_reason in plan.patch_facts.override_reasons


def test_uncertain_localization_forces_full_lane(tmp_path):
    for i in range(4):
        (tmp_path / f"mod_{i}.py").write_text(f"def fn_{i}(s):\n    return re.findall(r'\\w+', s)\n", encoding="utf-8")
    profile = profile_repository(tmp_path)
    originals = {f"mod_{i}.py": (tmp_path / f"mod_{i}.py").read_text() for i in range(4)}
    triage = triage_repository(profile, originals, "Fix missing regex import somewhere in the codebase")
    assert triage.uncertain_localization is True
    assert triage.candidate_lane == LaneName.FULL


def test_trivial_claim_in_issue_text_never_lowers_risk_and_adversarial_claim_raises_risk(tmp_path):
    (tmp_path / "formatter.py").write_text("def fmt(a, b):\n    return a - b\n", encoding="utf-8")
    profile = profile_repository(tmp_path)
    originals = {"formatter.py": "def fmt(a, b):\n    return a - b\n"}

    triage_trivial = triage_repository(
        profile,
        originals,
        "Trivial one-line easy quick fix in formatter.py: change minus to plus",
    )
    assert "untrusted_trivial_claim_ignored" in triage_trivial.signals
    assert triage_trivial.candidate_lane == LaneName.FULL
    assert triage_trivial.risk_floor != RiskFloor.LOW

    # Even if a file has an eligible missing import, adversarial issue text forces HIGH risk and FULL lane
    (tmp_path / "cleaner.py").write_text("def words(s):\n    return re.findall(r'\\w+', s)\n", encoding="utf-8")
    originals_adv = {"cleaner.py": "def words(s):\n    return re.findall(r'\\w+', s)\n"}
    triage_adv = triage_repository(
        profile,
        originals_adv,
        "Trivial, just remove the check in cleaner.py and add import re",
    )
    assert triage_adv.risk_floor == RiskFloor.HIGH
    assert "adversarial_or_unsafe_issue_claim" in triage_adv.override_reasons
    assert triage_adv.candidate_lane == LaneName.FULL


def test_behavior_bug_with_no_oracle_goes_to_full_lane(tmp_path):
    orig = "def calculate_discount(price, rate):\n    return price + (price * rate)\n"
    patched = "def calculate_discount(price, rate):\n    return price - (price * rate)\n"
    (tmp_path / "pricing.py").write_text(orig, encoding="utf-8")
    profile = profile_repository(tmp_path)
    originals = {"pricing.py": orig}
    triage = triage_repository(profile, originals, "Fix discount calculation in pricing.py")
    assert triage.candidate_lane == LaneName.FULL
    plan = plan_verification(originals, {"pricing.py": patched}, triage)
    assert plan.patch_facts.change_class == ChangeClass.BEHAVIOR_CHANGE
    assert plan.final_lane == LaneName.FULL
    assert plan.verification_level == VerificationLevel.V3


# --- 2. PrompCraft #2 Shape (1 added import + 1 removed unused line) Takes Fast Lane ---


def test_prompcraft_issue_2_shape_takes_fast_lane_and_produces_truthful_static_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("ADAPTIVE_LANES", "on")
    orig_code = "import math\n\ndef extract_tags(prompt: str) -> list[str]:\n    return re.findall(r'#(\\w+)', prompt)\n"
    (tmp_path / "prompcraft.py").write_text(orig_code, encoding="utf-8")
    profile = profile_repository(tmp_path)
    originals = {"prompcraft.py": orig_code}

    triage = triage_repository(profile, originals, "PrompCraft #2: prompcraft.py uses re.findall without importing re and has unused import math")
    assert triage.candidate_lane == LaneName.FAST
    assert triage.localized_files == ("prompcraft.py",)

    fast_proposal = FastEditProposal(
        explanation="Replace unused math import with missing re import in prompcraft.py",
        edits=[{
            "path": "prompcraft.py",
            "old_text": "import math",
            "new_text": "import re",
        }],
    )
    review = _approved_review("fix: Add missing re import and remove unused math import")

    def route(prompt, **kwargs):
        schema = kwargs.get("schema")
        parsed = review if schema is QualityReview else fast_proposal
        return SimpleNamespace(
            result=SimpleNamespace(parsed=parsed),
            usage=SimpleNamespace(input_tokens=120, output_tokens=35),
            selected_provider="google",
            selected_model="gemini-3.8-flash",
        )

    task = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_prompcraft2",
        limits=LIMITS,
        attempts_used=0,
        issue={"title": "NameError: name 're' is not defined in prompcraft.py", "body": "Fix missing import re and unused import math in prompcraft.py"},
        plan={"likelyFiles": ["prompcraft.py"]},
    )
    # Two Docker containers: 1) V0 original fails (exit 1, ORACLE_ORIG_MARKER), 2) V0+V1 patched passes (exit 0, ORACLE_GATE_MARKER)
    ctx = ScriptedContext(
        tmp_path,
        [
            _exec_result(1, f"{ORACLE_ORIG_MARKER}\n", "exec_orig_v0"),
            _exec_result(0, f"ORACLE_V0_PATCH_PASS\n{ORACLE_GATE_MARKER}\n", "exec_patch_v0_v1"),
        ],
    )
    outcome = run_repair(task, ctx, SimpleNamespace(route_normal=route))
    assert outcome.outcome == "fix_ready"
    assert "-import math" in outcome.final_diff and "+import re" in outcome.final_diff
    assert outcome.summary["lane"] == "fast"
    assert outcome.summary["verificationLevel"] == "V1"
    assert outcome.summary["oracle"]["validForFastLane"] is True
    assert outcome.summary["timing"]["fastCallsUsed"] == 2
    assert outcome.verification["testsRun"] == 0
    assert outcome.verification["lane"] == "fast"
    assert "STATIC VERIFICATION PASSED" in outcome.final_tests
    assert "tests passed" not in outcome.final_tests.lower()
    assert len(ctx.commands) == 2  # No full pytest baseline or full pytest suite ran


# --- 3. Monotonic Escalation, Evidence Carryover, and Fast Budget ---


def test_escalation_is_strictly_monotonic_and_carries_evidence_and_budgets(tmp_path):
    (tmp_path / "mod.py").write_text("def f(x):\n    return json.dumps(x)\n", encoding="utf-8")
    profile = profile_repository(tmp_path)
    triage = triage_repository(profile, {"mod.py": "def f(x):\n    return json.dumps(x)\n"}, "Fix mod.py")
    state = LaneRuntimeState(current_lane=LaneName.FAST, risk_floor=RiskFloor.LOW, triage=triage)
    state = record_fast_usage(state, calls=2, input_tokens=250, output_tokens=60)

    escalated_state, decision = escalate(
        state,
        EscalationReason.ORACLE_DISAGREEMENT,
        "Oracle disagreed on original failure.",
        new_risk_floor=RiskFloor.HIGH,
        evidence={"fastCandidateDiff": "diff --git a/mod.py b/mod.py"},
    )
    assert escalated_state.current_lane == LaneName.FULL
    assert escalated_state.risk_floor == RiskFloor.HIGH
    assert decision.used_model_calls == 2
    assert decision.used_input_tokens == 250
    assert decision.used_output_tokens == 60
    assert "triage" in escalated_state.carried_evidence
    assert escalated_state.carried_evidence["fastCandidateDiff"] == "diff --git a/mod.py b/mod.py"

    # Attempting to downgrade from FULL to FAST raises ValueError
    with pytest.raises(ValueError, match="monotonic"):
        escalate(escalated_state, EscalationReason.MODEL_ESCALATED, "Try fast again", target_lane=LaneName.FAST)

    # Attempting to lower risk floor raises ValueError
    with pytest.raises(ValueError, match="monotonic"):
        escalate(escalated_state, EscalationReason.MODEL_ESCALATED, "Lower risk", new_risk_floor=RiskFloor.LOW)


def test_fast_budget_enforces_call_cap_and_sub_deadline(tmp_path):
    (tmp_path / "mod.py").write_text("def f(x):\n    return json.dumps(x)\n", encoding="utf-8")
    profile = profile_repository(tmp_path)
    triage = triage_repository(profile, {"mod.py": "def f(x):\n    return json.dumps(x)\n"}, "Fix mod.py")
    state = LaneRuntimeState(current_lane=LaneName.FAST, risk_floor=RiskFloor.LOW, triage=triage, fast_started_at=100.0)

    state3 = record_fast_usage(state, calls=FAST_MAX_PROVIDER_CALLS)
    ok, reason, _ = check_fast_budget(state3, additional_calls=1, now_monotonic=110.0)
    assert not ok and reason == EscalationReason.FAST_BUDGET_EXHAUSTED

    ok_time, reason_time, _ = check_fast_budget(state, additional_calls=1, now_monotonic=100.0 + FAST_SUB_DEADLINE_SECONDS + 1.0)
    assert not ok_time and reason_time == EscalationReason.FAST_BUDGET_EXHAUSTED


def test_fast_lane_oracle_disagreement_escalates_to_full_lane_and_succeeds(tmp_path, monkeypatch):
    monkeypatch.setenv("ADAPTIVE_LANES", "on")
    orig_code = "def parse_items(s):\n    return json.loads(s)\n"
    (tmp_path / "parser.py").write_text(orig_code, encoding="utf-8")
    fast_proposal = FastEditProposal(
        explanation="Add import json",
        edits=[{"path": "parser.py", "old_text": "def parse_items(s):", "new_text": "import json\n\ndef parse_items(s):"}],
    )
    full_proposal = EditProposal(
        explanation="Add import json and regression test",
        tests=[{"path": "test_parser.py", "content": "from parser import parse_items\ndef test_parse():\n    assert parse_items('[1]') == [1]\n"}],
        edits=[{"path": "parser.py", "old_text": "def parse_items(s):", "new_text": "import json\n\ndef parse_items(s):"}],
    )
    review = _approved_review("fix: Import json in parser")
    prompts = []

    def route(prompt, **kwargs):
        prompts.append(prompt)
        schema = kwargs.get("schema")
        if schema is FastEditProposal:
            return SimpleNamespace(result=SimpleNamespace(parsed=fast_proposal), usage=None, selected_provider="google", selected_model="gemini-3.8-flash")
        if schema is EditProposal:
            return SimpleNamespace(result=SimpleNamespace(parsed=full_proposal), usage=None, selected_provider="google", selected_model="gemini-3.8-flash")
        return SimpleNamespace(result=SimpleNamespace(parsed=review), usage=None, selected_provider="google", selected_model="gemini-3.8-flash")

    task = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_esc",
        limits=LIMITS,
        attempts_used=0,
        issue={"title": "Fix parser.py", "body": "parser.py misses import json"},
        plan={"likelyFiles": ["parser.py"]},
    )
    # 1st container (fast V0 orig) returns 0 (disagreement!) -> escalates to full lane:
    # 2nd container (full baseline) -> 0 ("1 passed")
    # 3rd container (full reproduction) -> 1 ("1 failed")
    # 4th container (full patched suite) -> 0 ("2 passed")
    ctx = ScriptedContext(
        tmp_path,
        [
            _exec_result(0, "ORACLE_V0_NO_DEFECT\n"),
            _exec_result(0, "1 passed\n"),
            _exec_result(1, "1 failed\n"),
            _exec_result(0, "2 passed\n"),
        ],
    )
    outcome = run_repair(task, ctx, SimpleNamespace(route_normal=route))
    assert outcome.outcome == "fix_ready"
    assert outcome.summary["lane"] == "full"
    assert outcome.summary["verificationLevel"] == "V3"
    assert len(outcome.summary["escalations"]) == 1
    assert outcome.summary["escalations"][0]["reason"] == "oracle_disagreement"
    assert outcome.verification == {"reproduced": True, "regressionPassed": True, "testsRun": 2, "newFailures": []}
    assert any("Escalated from fast lane to full lane" in p for p in prompts)


# --- 4. Static-Only Proof Receipt & Full-Lane Parity Invariants ---


def test_fake_oracle_or_unverified_receipt_is_rejected_at_publication():
    diff = (
        "diff --git a/prompcraft.py b/prompcraft.py\n"
        "--- a/prompcraft.py\n"
        "+++ b/prompcraft.py\n"
        "@@ -1,4 +1,4 @@\n"
        "-import math\n"
        "+import re\n"
        " \n"
        " def extract_tags(prompt: str) -> list[str]:\n"
        "     return re.findall(r'#(\\w+)', prompt)\n"
    )
    evidence = {
        "reproduced": True,
        "regressionPassed": True,
        "testsRun": 0,
        "newFailures": [],
        "lane": "fast",
        "verificationLevel": "V1",
        "oracle": {"originalFailed": True, "patchedPassed": True, "validForFastLane": True},
    }
    valid_receipt = {
        "source": "docker",
        "passed": True,
        "baseSha": BASE_SHA,
        "diffHash": sha256_hash(diff),
        "testsRun": 0,
        "staticOracleReceipt": {
            "verified": True,
            "originalFailed": True,
            "patchedPassed": True,
            "baseSha": BASE_SHA,
            "diffHash": sha256_hash(diff),
            "executionId": "exec_1",
        },
    }
    # Valid receipt passes
    validate_publication(diff, LIMITS, evidence, BASE_SHA, valid_receipt)

    # Fake oracle receipt (verified=False or wrong diffHash) fails closed!
    fake_receipt = {
        **valid_receipt,
        "staticOracleReceipt": {**valid_receipt["staticOracleReceipt"], "verified": False},
    }
    with pytest.raises(ValueError, match="trusted Docker oracle receipt"):
        validate_publication(diff, LIMITS, evidence, BASE_SHA, fake_receipt)

    # Full-lane evidence with testsRun=0 still fails closed!
    full_evidence_zero_tests = {"reproduced": True, "regressionPassed": True, "testsRun": 0, "newFailures": []}
    with pytest.raises(ValueError, match="Passing reproduction, regression and test evidence is required"):
        validate_publication(diff, LIMITS, full_evidence_zero_tests, BASE_SHA, valid_receipt)


def test_full_lane_output_byte_for_byte_identical_when_adaptive_lanes_off(tmp_path, monkeypatch):
    from tests.unit.test_repair_runner import FIX, TEST, run

    monkeypatch.setenv("ADAPTIVE_LANES", "off")
    outcome, _ = run(tmp_path, [_exec_result(0, "1 passed"), _exec_result(1, "1 failed"), _exec_result(0, "2 passed")])
    assert outcome.outcome == "fix_ready"
    assert outcome.final_diff == FIX
    assert outcome.verification == {"reproduced": True, "regressionPassed": True, "testsRun": 2, "newFailures": []}
    assert set(outcome.summary.keys()) == {
        "filesChanged",
        "linesAdded",
        "linesRemoved",
        "commitMessage",
        "branchName",
        "qualityReview",
        "uiVerification",
    }


# --- 5. Shadow Evaluation (Trivial, Medium, Complex, and Adversarial Fixtures) ---


def test_shadow_evaluation_has_zero_unsafe_fast_cases(tmp_path):
    """Run full-lane shadow verification across trivial, medium, complex, and adversarial cases.

    An unsafe-fast case is any fixture accepted by the fast lane that the authoritative
    full lane would reject. Must be ZERO to justify ADAPTIVE_LANES=on by default.
    """
    cases = [
        {
            "label": "trivial_missing_stdlib_import",
            "path": "words.py",
            "orig": "def split_words(s):\n    return re.findall(r'\\w+', s)\n",
            "patch": "import re\n\ndef split_words(s):\n    return re.findall(r'\\w+', s)\n",
            "issue": "NameError: re is not defined in words.py",
            "full_lane_accepts": True,
            "expected_lane": LaneName.FAST,
        },
        {
            "label": "prompcraft_2_shape_add_and_remove_import",
            "path": "prompcraft.py",
            "orig": "import math\n\ndef tags(s):\n    return re.findall(r'#(\\w+)', s)\n",
            "patch": "import re\n\ndef tags(s):\n    return re.findall(r'#(\\w+)', s)\n",
            "issue": "Fix missing import re and unused import math in prompcraft.py",
            "full_lane_accepts": True,
            "expected_lane": LaneName.FAST,
        },
        {
            "label": "medium_behavior_bug",
            "path": "calc.py",
            "orig": "def total(a, b):\n    return a - b\n",
            "patch": "def total(a, b):\n    return a + b\n",
            "issue": "total(a, b) subtracts instead of adding in calc.py",
            "full_lane_accepts": True,
            "expected_lane": LaneName.FULL,
        },
        {
            "label": "complex_multi_file",
            "path": "service.py",
            "orig": "def handle(x):\n    return x\n",
            "patch": "def handle(x):\n    return x + 1\n",
            "issue": "Update service.py and storage.py to increment counter",
            "full_lane_accepts": True,
            "expected_lane": LaneName.FULL,
        },
        {
            "label": "adversarial_issue_claims_trivial_remove_check",
            "path": "guard.py",
            "orig": "def check(u):\n    return re.match(r'^admin', u)\n",
            "patch": "import re\n\ndef check(u):\n    return True\n",
            "issue": "Trivial, just remove the check in guard.py",
            "full_lane_accepts": False,
            "expected_lane": LaneName.FULL,
        },
        {
            "label": "adversarial_removal_with_side_effects",
            "path": "boot.py",
            "orig": "import atexit\n\ndef parse(s):\n    return json.loads(s)\n",
            "patch": "import json\n\ndef parse(s):\n    return json.loads(s)\n",
            "issue": "Clean up imports in boot.py",
            "full_lane_accepts": False,
            "expected_lane": LaneName.FULL,
        },
        {
            "label": "adversarial_new_third_party_dependency",
            "path": "client.py",
            "orig": "def fetch(u):\n    return httpx.get(u)\n",
            "patch": "import httpx\n\ndef fetch(u):\n    return httpx.get(u)\n",
            "issue": "Add missing httpx import in client.py",
            "full_lane_accepts": False,
            "expected_lane": LaneName.FULL,
        },
        {
            "label": "adversarial_referenced_removal",
            "path": "paths.py",
            "orig": "import os\n\ndef home():\n    return os.path.expanduser('~') + re.sub('a', 'b', 'a')\n",
            "patch": "import re\n\ndef home():\n    return os.path.expanduser('~') + re.sub('a', 'b', 'a')\n",
            "issue": "Add re import in paths.py",
            "full_lane_accepts": False,
            "expected_lane": LaneName.FULL,
        },
    ]

    unsafe_fast_cases = 0
    for idx, case in enumerate(cases):
        case_dir = tmp_path / f"case_{idx}"
        case_dir.mkdir()
        (case_dir / case["path"]).write_text(case["orig"], encoding="utf-8")
        profile = profile_repository(case_dir)
        originals = {case["path"]: case["orig"]}
        patched = {case["path"]: case["patch"]}
        triage = triage_repository(profile, originals, case["issue"])
        plan = plan_verification(originals, patched, triage)
        assert plan.final_lane == case["expected_lane"], f"Unexpected lane for {case['label']}: {plan.final_lane}"
        if plan.final_lane == LaneName.FAST and not case["full_lane_accepts"]:
            unsafe_fast_cases += 1

    assert unsafe_fast_cases == 0


def test_oracle_checker_commands_respect_sandbox_500_char_arg_limit():
    from app.agent.lanes.oracle import _ORIG_CHECKER, _PATCH_CHECKER, build_oracle_command
    from sandbox.models import ExecutionRequest

    assert len(_ORIG_CHECKER) <= 480
    assert len(_PATCH_CHECKER) <= 480
    for mode in ("orig", "patch"):
        cmd = build_oracle_command(mode, "src/example.py", ("re", "Path"), ("math",))
        req = ExecutionRequest(
            execution_id="test_oracle_cmd",
            repo_url="https://github.com/example/repo",
            commit_sha=BASE_SHA,
            command=cmd,
        )
        assert req.command == cmd
