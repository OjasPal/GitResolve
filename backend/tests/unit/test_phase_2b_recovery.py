"""Phase 2B deterministic unit tests: StoreRepairCheckpointer, reaper-driven resume, fencing, and Adaptive Lanes alignment."""
from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent.graph_engine.checkpointer import (
    StoreRepairCheckpointer,
    make_thread_id,
)
from app.agent.graph_engine.engine import run_repair_graph
from app.agent.graph_engine.state import (
    CHECKPOINT_SCHEMA_VERSION,
    LANES_POLICY_VERSION,
    MAX_CHECKPOINT_BYTES,
    MAX_PRESERVED_EVIDENCE_BYTES,
    validate_checkpoint_payload,
)
from app.agent.graph_engine.tools import RepairActionEnvelope
from app.agent.lanes import (
    EscalationReason,
    LaneName,
    LaneRuntimeState,
    ORACLE_GATE_MARKER,
    ORACLE_ORIG_MARKER,
    RiskFloor,
    TriageReport,
    escalate,
)
from app.agent.repair_runner import EditProposal, FastEditProposal, QualityReview
from app.core.config import Settings
from app.schemas.enums import EventLevel, EventType, FailureCode, TaskStage, TaskStatus
from app.services.ids import sha256_hash
from app.services.store.memory import MemoryStore
from app.services.transitions import EventSpec, LeaseLost
from app.services.worker_service import WorkerService


BASE_SHA = "b" * 40
LIMITS = {
    "maxRepairAttempts": 2,
    "maxDiffFiles": 5,
    "maxDiffLines": 150,
    "maxGraphSteps": 40,
    "maxToolCalls": 24,
    "maxModelCalls": 16,
    "maxTotalTokens": 250000,
    "maxRuntimeSeconds": 1800,
    "taskTimeoutSeconds": 1800,
}


def _exec_result(code: int, stdout: str = "", exec_id: str = "exec_docker_1") -> SimpleNamespace:
    return SimpleNamespace(
        status="passed" if code == 0 else "failed",
        exit_code=code,
        stdout=stdout,
        stderr="",
        oom_killed=False,
        cleanup_ok=True,
        execution_id=exec_id,
    )


def _oracle_exec_results() -> list[SimpleNamespace]:
    return [
        _exec_result(1, f"{ORACLE_ORIG_MARKER}\n", "exec_orig_v0"),
        _exec_result(0, f"ORACLE_V0_PATCH_PASS\n{ORACLE_GATE_MARKER}\n", "exec_patch_v0_v1"),
    ]


def _approved_review(commit_msg: str = "fix: resolve issue", branch: str = "fix-issue") -> QualityReview:
    return QualityReview(
        commit_message=commit_msg,
        branch_name=branch,
        approved=True,
        requirements=[
            {
                "requirement": "Resolve reported defect cleanly",
                "status": "passed",
                "evidence": "Verified in Docker container.",
            }
        ],
        blocking_issues=[],
    )


class ScriptedRouter:
    """Deterministic scripted router for Phase 2B recovery tests (0 live LLM calls)."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.schemas: list[Any] = []

    def route_normal(self, prompt: str, **kwargs: Any) -> SimpleNamespace:
        self.prompts.append(prompt)
        schema = kwargs.get("schema")
        self.schemas.append(schema)
        if not self.responses:
            if schema is QualityReview:
                item: Any = _approved_review()
            else:
                raise AssertionError(f"ScriptedRouter exhausted at call #{len(self.prompts)} (schema={schema})")
        else:
            item = self.responses.pop(0)
        if callable(item):
            item = item(prompt, kwargs)
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
        )


class ClockController:
    """Mutable UTC wall clock for testing lease expiration and fast sub-deadlines."""

    def __init__(self, initial: datetime | None = None) -> None:
        self.now = initial or datetime(2026, 11, 7, 0, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> datetime:
        self.now = self.now + timedelta(seconds=seconds)
        return self.now


class StoreBackedWorkerContext:
    """Worker execution context wired directly to WorkerService + MemoryStore for realistic fencing and reaper tests."""

    def __init__(
        self,
        svc: WorkerService,
        task_id: str,
        worker_id: str,
        root: Path,
        exec_results: list[SimpleNamespace],
    ) -> None:
        self._svc = svc
        self.task_id = task_id
        self.worker_id = worker_id
        self.root = root
        self.results = list(exec_results)
        self.commands: list[dict[str, Any]] = []
        self.events: list[tuple[Any, str, dict[str, Any]]] = []
        self.stages: list[Any] = []
        self.artifacts: dict[str, str] = {}
        self.attempt_count = 0
        self.prepare_dependencies = False
        self.require_ui_checks = False
        self.sandbox_only_tests = False
        self.last_verification_receipt: dict[str, Any] | None = None

    def checkpoint(self) -> None:
        self._svc.heartbeat(self.task_id, self.worker_id)

    def usage(self, attempts_used: int | None = None, **kwargs: Any) -> None:
        allowed_keys = {"modelCalls", "inputTokens", "outputTokens", "execSeconds"}
        filtered = {k: v for k, v in kwargs.items() if k in allowed_keys}
        self._svc.update_progress(
            self.task_id,
            self.worker_id,
            attempts_used=attempts_used,
            budgets=filtered or None,
        )
        if attempts_used is not None:
            self.attempt_count = attempts_used

    def stage(self, stage: TaskStage, **kwargs: Any) -> None:
        self.checkpoint()
        self.stages.append(stage)
        self._svc.update_progress(self.task_id, self.worker_id, stage=stage, fields=kwargs or None)

    def event(
        self,
        kind: EventType,
        message: str,
        level: EventLevel = EventLevel.INFO,
        data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.events.append((kind, message, data or {}))
        self._svc.append_event(
            self.task_id,
            self.worker_id,
            EventSpec(kind, message, level, data or {}),
        )

    def artifact(self, artifact_id: str, kind: Any, content: str, attempt: int | None = None) -> None:
        self.checkpoint()
        self.artifacts[artifact_id] = content

    def record_lane_state(self, lane_payload: dict[str, Any]) -> dict[str, Any]:
        return self._svc.record_lane_state(self.task_id, self.worker_id, lane_payload)

    @contextmanager
    def repository(self):
        yield self.root

    def execute(self, **kwargs: Any) -> SimpleNamespace:
        self.checkpoint()
        self.commands.append(kwargs)
        if not self.results:
            res = _exec_result(0, "1 passed")
        else:
            res = self.results.pop(0)
        patch_str = kwargs.get("patch") or ""
        commit_sha = kwargs.get("commit_sha") or BASE_SHA
        is_oracle = ORACLE_GATE_MARKER in (res.stdout or "") or ORACLE_ORIG_MARKER in (res.stdout or "")
        static_receipt = None
        if is_oracle and patch_str and res.exit_code == 0:
            static_receipt = {
                "verified": True,
                "originalFailed": True,
                "patchedPassed": True,
                "baseSha": commit_sha,
                "diffHash": sha256_hash(patch_str),
                "executionId": getattr(res, "execution_id", f"exec_{len(self.commands)}"),
            }
        receipt = self._svc.record_sandbox(
            self.task_id,
            self.worker_id,
            {
                "source": "docker",
                "passed": res.exit_code == 0,
                "baseSha": commit_sha,
                "diffHash": sha256_hash(patch_str),
                "testsRun": 0 if is_oracle else 1,
                "staticOracleReceipt": static_receipt,
            },
        )
        self.last_verification_receipt = receipt
        return res


def _make_worker_service(store: MemoryStore, clock: ClockController, *, lease_seconds: int = 30) -> WorkerService:
    return WorkerService(store, Settings(lease_seconds=lease_seconds), clock=clock)


def _seed_execute_task(
    store: MemoryStore,
    clock: ClockController,
    task_id: str = "tsk_phase2b",
    *,
    issue_title: str = "Fix bug",
    issue_body: str = "Fix defect",
) -> dict[str, Any]:
    now = clock()
    store.set("users/usr_test", {"id": "usr_test", "activeTaskId": task_id})
    doc = {
        "id": task_id,
        "taskId": task_id,
        "userId": "usr_test",
        "status": TaskStatus.RUNNING.value,
        "stage": TaskStage.SETUP.value,
        "dispatchPending": True,
        "dispatchPhase": "execute",
        "leaseOwner": None,
        "leaseExpiresAt": None,
        "heartbeatAt": None,
        "baseSha": BASE_SHA,
        "limits": copy.deepcopy(LIMITS),
        "attempts": {"used": 0, "max": 2},
        "crashResumes": 0,
        "budgets": {
            "modelCalls": 0,
            "inputTokens": 0,
            "outputTokens": 0,
            "execSeconds": 0.0,
        },
        "issue": {"title": issue_title, "body": issue_body},
        "plan": {"steps": [issue_title]},
        "deadlineAt": now + timedelta(minutes=30),
        "createdAt": now,
        "updatedAt": now,
    }
    store.set(f"tasks/{task_id}", doc)
    return doc


# --- 1. Kill Worker Mid-Tool-Loop -> Reaper Requeues -> Second Worker Resumes & Completes ---


def test_kill_worker_mid_tool_loop_reaper_requeues_and_resume_completes_without_repeating_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_ENGINE", "graph")
    monkeypatch.setenv("ADAPTIVE_LANES", "off")
    (tmp_path / "calc.py").write_text("def multiply(a, b):\n    return a + b\n", encoding="utf-8")

    clock = ClockController()
    store = MemoryStore()
    svc = _make_worker_service(store, clock)
    task_id = "tsk_mid_tool_crash"
    _seed_execute_task(store, clock, task_id, issue_title="multiply in calc.py adds instead of multiplying")

    # Worker 1 claims the task
    claimed_w1 = svc.claim_next("worker_1")
    assert claimed_w1 is not None and claimed_w1.task["leaseOwner"] == "worker_1"

    task_obj = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id=task_id,
        limits=LIMITS,
        attempts_used=0,
        issue={"title": "multiply in calc.py adds instead of multiplying", "body": "Fix multiply in calc.py."},
        plan={},
    )

    # Worker 1 executes 2 tool turns (read_file + edit_file), checkpoints both to Store, then crashes before turn 3!
    w1_router = ScriptedRouter([
        RepairActionEnvelope(
            action="read_file",
            args={"path": "calc.py", "start": 1, "end": 10},
            summary="Read calc.py",
        ),
        RepairActionEnvelope(
            action="edit_file",
            args={"path": "calc.py", "search": "    return a + b\n", "replace": "    return a * b\n"},
            summary="Fix multiply implementation",
        ),
    ])
    ctx_w1 = StoreBackedWorkerContext(
        svc,
        task_id,
        "worker_1",
        tmp_path,
        [_exec_result(0, "1 passed")],  # baseline run on Worker 1
    )

    with pytest.raises(RuntimeError, match="Simulated crash after tool turn 2"):
        run_repair_graph(task_obj, ctx_w1, w1_router, crash_after_tool_turn=2)

    assert len(w1_router.prompts) == 2
    assert len(ctx_w1.commands) == 1  # baseline ran once on worker 1

    # Verify compact checkpoint was persisted in the store
    ckpt_doc = store.get(f"tasks/{task_id}/checkpoints/execute")
    assert ckpt_doc is not None
    assert ckpt_doc["hasCheckpoint"] is True
    assert ckpt_doc["byteSize"] < MAX_CHECKPOINT_BYTES

    # Advance clock past Worker 1's lease; reaper runs and requeues instead of marking worker_lost
    clock.advance(40)
    reaped = svc.reap_expired()
    assert reaped == [task_id]
    reaped_doc = store.get(f"tasks/{task_id}")
    assert reaped_doc["status"] == TaskStatus.RUNNING
    assert reaped_doc["dispatchPending"] is True
    assert reaped_doc["dispatchPhase"] == "execute"
    assert reaped_doc["crashResumes"] == 1
    assert reaped_doc["attempts"]["used"] == 0  # Crash resume is NOT a repair attempt!

    # Worker 2 claims the requeued task and resumes from the checkpoint
    claimed_w2 = svc.claim_next("worker_2")
    assert claimed_w2 is not None and claimed_w2.task["leaseOwner"] == "worker_2"

    # Worker 2 only needs to supply the remaining tool actions (create_file + finish + review)
    w2_router = ScriptedRouter([
        RepairActionEnvelope(
            action="create_file",
            args={
                "path": "test_calc.py",
                "content": "from calc import multiply\ndef test_multiply():\n    assert multiply(3, 4) == 12\n",
            },
            summary="Create regression test",
        ),
        RepairActionEnvelope(
            action="finish",
            args={"explanation": "Fixed multiply in calc.py to use * operator."},
            summary="Finish repair",
        ),
        _approved_review("fix: multiply values in calc.py", "fix-calc-multiply"),
    ])
    ctx_w2 = StoreBackedWorkerContext(
        svc,
        task_id,
        "worker_2",
        tmp_path,
        [
            _exec_result(1, "1 failed"),  # reproduce
            _exec_result(0, "2 passed"),  # verify
        ],
    )

    outcome = run_repair_graph(task_obj, ctx_w2, w2_router)
    assert outcome.outcome == "fix_ready"
    assert "-    return a + b" in outcome.final_diff
    assert "+    return a * b" in outcome.final_diff
    # Worker 2 did NOT re-run baseline or the first 2 tool calls
    assert len(w2_router.prompts) == 3
    assert len(ctx_w2.commands) == 2


# --- 2. Stale Worker Fencing & Bounded Crash Resume Exhaustion ---


def test_stale_worker_writes_rejected_and_bounded_crash_resumes_transition_to_worker_lost(
    tmp_path: Path,
) -> None:
    clock = ClockController()
    store = MemoryStore()
    svc = _make_worker_service(store, clock)
    task_id = "tsk_fencing"
    _seed_execute_task(store, clock, task_id)

    svc.claim_next("worker_stale")
    checkpointer_stale = StoreRepairCheckpointer(
        store,
        task_id=task_id,
        worker_id="worker_stale",
        clock=clock,
    )
    thread_id = make_thread_id(task_id, "execute")
    checkpointer_stale.pin_or_verify_thread(
        thread_id,
        engine_mode="graph",
        base_sha=BASE_SHA,
        schema_version=CHECKPOINT_SCHEMA_VERSION,
    )
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    checkpoint_payload = {
        "v": 1,
        "id": "ckpt_1",
        "ts": "2026-11-07T00:00:00Z",
        "channel_values": {
            "task_id": task_id,
            "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
            "lanes_policy_version": LANES_POLICY_VERSION,
            "lane": "full",
        },
        "channel_versions": {},
        "versions_seen": {},
        "pending_sends": [],
    }
    checkpointer_stale.put(config, checkpoint_payload, {"step": 1}, {})

    # Lease expires; reaper requeues (resume #1), and worker_new claims ownership
    clock.advance(40)
    reaped_1 = svc.reap_expired()
    assert reaped_1 == [task_id]
    assert store.get(f"tasks/{task_id}")["crashResumes"] == 1
    svc.claim_next("worker_new")

    # Stale worker attempts to write checkpoint, lane state, and sandbox receipt -> all rejected with LeaseLost!
    with pytest.raises(LeaseLost):
        checkpointer_stale.put(config, {**checkpoint_payload, "id": "ckpt_stale"}, {"step": 2}, {})

    with pytest.raises(LeaseLost):
        svc.record_lane_state(task_id, "worker_stale", {"lane": "fast", "riskFloor": "low"})

    with pytest.raises(LeaseLost):
        svc.record_sandbox(
            task_id,
            "worker_stale",
            {"source": "docker", "passed": True, "baseSha": BASE_SHA, "diffHash": "abc"},
        )

    # Lease expires a second time -> resume #2 allowed (maxCrashResumes == 2)
    clock.advance(40)
    reaped_2 = svc.reap_expired()
    assert reaped_2 == [task_id]
    doc_2 = store.get(f"tasks/{task_id}")
    assert doc_2["status"] == TaskStatus.RUNNING
    assert doc_2["crashResumes"] == 2
    svc.claim_next("worker_3")

    # Lease expires a third time (crashResumes == 2 >= maxCrashResumes) -> transitions to WORKER_LOST with sanitized outcome
    clock.advance(40)
    reaped_3 = svc.reap_expired()
    assert reaped_3 == [task_id]
    doc_3 = store.get(f"tasks/{task_id}")
    assert doc_3["status"] == TaskStatus.WORKER_LOST
    assert doc_3["outcome"]["kind"] == "cannot_fix_with_report"
    assert doc_3["failure"]["code"] == FailureCode.WORKER_LOST


# --- 3. Duplicate Resume Idempotency & Static Oracle Receipt Idempotency ---


def test_duplicate_resume_and_static_oracle_receipts_are_idempotent_per_diff_hash(
    tmp_path: Path,
) -> None:
    clock = ClockController()
    store = MemoryStore()
    svc = _make_worker_service(store, clock)
    task_id = "tsk_receipt_idempotent"
    _seed_execute_task(store, clock, task_id)
    svc.claim_next("worker_1")

    diff_hash_a = sha256_hash("diff-a")
    diff_hash_b = sha256_hash("diff-b")

    r1 = svc.record_sandbox(
        task_id,
        "worker_1",
        {
            "source": "docker",
            "passed": True,
            "baseSha": BASE_SHA,
            "diffHash": diff_hash_a,
            "testsRun": 0,
            "staticOracleReceipt": {
                "verified": True,
                "originalFailed": True,
                "patchedPassed": True,
                "baseSha": BASE_SHA,
                "diffHash": diff_hash_a,
                "executionId": "exec_first",
            },
        },
    )
    r2 = svc.record_sandbox(
        task_id,
        "worker_1",
        {
            "source": "docker",
            "passed": True,
            "baseSha": BASE_SHA,
            "diffHash": diff_hash_a,
            "testsRun": 0,
            "staticOracleReceipt": {
                "verified": True,
                "originalFailed": True,
                "patchedPassed": True,
                "baseSha": BASE_SHA,
                "diffHash": diff_hash_a,
                "executionId": "exec_duplicate_on_resume",
            },
        },
    )
    # Duplicate resume for the same (baseSha, diffHash) preserves the original receiptId and executionId
    assert r1["receiptId"] == r2["receiptId"]
    assert r2["staticOracleReceipt"]["executionId"] == "exec_first"

    # A different diffHash mints a distinct receipt and cannot reuse the old one
    r3 = svc.record_sandbox(
        task_id,
        "worker_1",
        {
            "source": "docker",
            "passed": True,
            "baseSha": BASE_SHA,
            "diffHash": diff_hash_b,
            "testsRun": 0,
            "staticOracleReceipt": {
                "verified": True,
                "originalFailed": True,
                "patchedPassed": True,
                "baseSha": BASE_SHA,
                "diffHash": diff_hash_b,
                "executionId": "exec_second_diff",
            },
        },
    )
    assert r3["receiptId"] != r1["receiptId"]
    assert r3["staticOracleReceipt"]["diffHash"] == diff_hash_b


# --- 4. (AL) Kill Mid-FAST Lane (Before V0 and After V0) -> Resumes in Fast Lane Without Second Patch Call ---


@pytest.mark.parametrize("crash_node", ["fast_static_oracle", "fast_review"])
def test_kill_mid_fast_lane_resumes_in_fast_lane_with_same_diff_hash_and_single_patch_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crash_node: str
) -> None:
    monkeypatch.setenv("AGENT_ENGINE", "graph")
    monkeypatch.setenv("ADAPTIVE_LANES", "on")
    # Missing stdlib import (`os.path.basename`) triggers low-risk fast lane with V0/V1 oracle
    (tmp_path / "paths.py").write_text(
        "def leaf_name(raw_path: str) -> str:\n    return os.path.basename(raw_path)\n",
        encoding="utf-8",
    )

    clock = ClockController()
    store = MemoryStore()
    svc = _make_worker_service(store, clock)
    task_id = f"tsk_fast_resume_{crash_node}"
    _seed_execute_task(
        store,
        clock,
        task_id,
        issue_title="NameError: name 'os' is not defined in paths.py",
        issue_body="leaf_name in paths.py uses os.path.basename without importing os.",
    )
    svc.claim_next("worker_1")

    task_obj = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id=task_id,
        limits=LIMITS,
        attempts_used=0,
        issue={
            "title": "NameError: name 'os' is not defined in paths.py",
            "body": "leaf_name in paths.py uses os.path.basename without importing os.",
        },
        plan={"files": ["paths.py"]},
    )

    fast_proposal = FastEditProposal(
        explanation="Import os in paths.py.",
        edits=[
            {
                "path": "paths.py",
                "old_text": "def leaf_name(raw_path: str) -> str:\n",
                "new_text": "import os\n\n\ndef leaf_name(raw_path: str) -> str:\n",
            }
        ],
    )

    # Worker 1 runs until crash_node
    w1_router = ScriptedRouter([fast_proposal])
    ctx_w1 = StoreBackedWorkerContext(
        svc,
        task_id,
        "worker_1",
        tmp_path,
        _oracle_exec_results(),
    )
    with pytest.raises(RuntimeError, match=f"Simulated crash before node {crash_node}"):
        run_repair_graph(task_obj, ctx_w1, w1_router, crash_before_node=crash_node)

    # Patch model call happened once on Worker 1
    assert len(w1_router.prompts) == 1

    # Reap and claim on Worker 2 within the 120s fast-lane sub-deadline (lease_seconds=30 < 40s < 120s)
    clock.advance(40)
    reaped = svc.reap_expired()
    assert reaped == [task_id]
    assert store.get(f"tasks/{task_id}")["crashResumes"] == 1
    svc.claim_next("worker_2")

    # Worker 2 router ONLY contains the QualityReview; if it attempts a second patch model call, it would fail!
    w2_router = ScriptedRouter([_approved_review("fix: import os in paths.py", "fix-paths-import")])
    ctx_w2 = StoreBackedWorkerContext(
        svc,
        task_id,
        "worker_2",
        tmp_path,
        _oracle_exec_results(),
    )
    outcome = run_repair_graph(task_obj, ctx_w2, w2_router)
    assert outcome.outcome == "fix_ready"
    assert outcome.summary["lane"] == "fast"
    assert "+import os" in outcome.final_diff
    assert len(w2_router.prompts) == 1  # Only review call ran on resume!
    if crash_node == "fast_review":
        # V0/V1 oracle was already checkpointed and valid for (base_sha, diff_hash), so Docker was NOT re-run on Worker 2
        assert len(ctx_w2.commands) == 0
    else:
        assert len(ctx_w2.commands) == 2


# --- 5. (AL) Kill After Escalation -> Resumes in Full Lane, Preserves Risk Floor & Escalations ---


def test_kill_after_escalation_resumes_in_full_lane_with_risk_floor_and_no_duplicate_escalation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_ENGINE", "graph")
    monkeypatch.setenv("ADAPTIVE_LANES", "on")
    (tmp_path / "paths.py").write_text(
        "def leaf_name(raw_path: str) -> str:\n    return os.path.basename(raw_path)\n",
        encoding="utf-8",
    )

    clock = ClockController()
    store = MemoryStore()
    svc = _make_worker_service(store, clock)
    task_id = "tsk_post_escalation_crash"
    _seed_execute_task(
        store,
        clock,
        task_id,
        issue_title="NameError: name 'os' is not defined in paths.py",
        issue_body="leaf_name in paths.py uses os.path.basename without importing os.",
    )
    svc.claim_next("worker_1")

    task_obj = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id=task_id,
        limits=LIMITS,
        attempts_used=0,
        issue={
            "title": "NameError: name 'os' is not defined in paths.py",
            "body": "leaf_name in paths.py uses os.path.basename without importing os.",
        },
        plan={"files": ["paths.py"]},
    )

    # Model requests escalation in fast_patch_action -> escalates fast->full -> crashes before explore!
    escalating_fast_proposal = FastEditProposal(
        explanation="Needs full suite verification.",
        edits=[],
        escalate_reason="Requires full regression test suite coverage.",
    )
    w1_router = ScriptedRouter([escalating_fast_proposal])
    ctx_w1 = StoreBackedWorkerContext(svc, task_id, "worker_1", tmp_path, [])

    with pytest.raises(RuntimeError, match="Simulated crash before node explore"):
        run_repair_graph(task_obj, ctx_w1, w1_router, crash_before_node="explore")

    # Reaper requeues
    clock.advance(200)
    svc.reap_expired()
    svc.claim_next("worker_2")

    full_proposal = EditProposal(
        explanation="Import os and add regression test.",
        tests=[
            {
                "path": "test_paths.py",
                "content": "from paths import leaf_name\ndef test_leaf():\n    assert leaf_name('a/b.txt') == 'b.txt'\n",
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
    w2_router = ScriptedRouter([full_proposal, _approved_review("fix: import os in paths.py", "fix-paths")])
    ctx_w2 = StoreBackedWorkerContext(
        svc,
        task_id,
        "worker_2",
        tmp_path,
        [
            _exec_result(0, "1 passed"),  # baseline in explore
            _exec_result(1, "1 failed"),  # reproduce
            _exec_result(0, "2 passed"),  # verify
        ],
    )
    outcome = run_repair_graph(task_obj, ctx_w2, w2_router)
    assert outcome.outcome == "fix_ready"
    assert outcome.summary["lane"] == "full"
    escalations = outcome.summary["escalations"]
    assert len(escalations) == 1
    assert escalations[0]["reason"] == EscalationReason.MODEL_ESCALATED.value

    # Verify task document recorded durable lane state with medium/high risk floor and single escalation
    saved_task = store.get(f"tasks/{task_id}")
    assert saved_task["laneState"]["lane"] == "full"
    assert saved_task["laneState"]["riskFloor"] in {"medium", "high"}
    assert len(saved_task["laneState"]["escalations"]) == 1


# --- 6. (AL) Wall-Clock Sub-Deadline Expiry Across Downtime Escalates to Full Lane ---


def test_fast_sub_deadline_wall_clock_expiry_during_downtime_escalates_to_full_lane_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_ENGINE", "graph")
    monkeypatch.setenv("ADAPTIVE_LANES", "on")
    (tmp_path / "paths.py").write_text(
        "def leaf_name(raw_path: str) -> str:\n    return os.path.basename(raw_path)\n",
        encoding="utf-8",
    )

    clock = ClockController()
    store = MemoryStore()
    svc = _make_worker_service(store, clock)
    task_id = "tsk_fast_deadline_downtime"
    _seed_execute_task(
        store,
        clock,
        task_id,
        issue_title="NameError: name 'os' is not defined in paths.py",
        issue_body="leaf_name in paths.py uses os.path.basename without importing os.",
    )
    svc.claim_next("worker_host_a")

    task_obj = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id=task_id,
        limits=LIMITS,
        attempts_used=0,
        issue={
            "title": "NameError: name 'os' is not defined in paths.py",
            "body": "leaf_name in paths.py uses os.path.basename without importing os.",
        },
        plan={"files": ["paths.py"]},
    )

    fast_proposal = FastEditProposal(
        explanation="Import os in paths.py.",
        edits=[
            {
                "path": "paths.py",
                "old_text": "def leaf_name(raw_path: str) -> str:\n",
                "new_text": "import os\n\n\ndef leaf_name(raw_path: str) -> str:\n",
            }
        ],
    )
    w1_router = ScriptedRouter([fast_proposal])
    ctx_w1 = StoreBackedWorkerContext(svc, task_id, "worker_host_a", tmp_path, [])

    with pytest.raises(RuntimeError, match="Simulated crash before node fast_static_oracle"):
        run_repair_graph(task_obj, ctx_w1, w1_router, crash_before_node="fast_static_oracle")

    # Advance wall clock by 200s (> 120s FAST_SUB_DEADLINE_SECONDS and > worker lease)
    clock.advance(200)
    svc.reap_expired()
    svc.claim_next("worker_host_b")

    # On worker_host_b, fast sub-deadline has expired during downtime -> must escalate to full lane (FAST_BUDGET_EXHAUSTED)!
    full_proposal = EditProposal(
        explanation="Import os and add regression test in full lane.",
        tests=[
            {
                "path": "test_paths.py",
                "content": "from paths import leaf_name\ndef test_leaf():\n    assert leaf_name('a/b.txt') == 'b.txt'\n",
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
    w2_router = ScriptedRouter([full_proposal, _approved_review("fix: import os in paths.py", "fix-paths")])
    ctx_w2 = StoreBackedWorkerContext(
        svc,
        task_id,
        "worker_host_b",
        tmp_path,
        [
            _exec_result(0, "1 passed"),  # baseline
            _exec_result(1, "1 failed"),  # reproduce
            _exec_result(0, "2 passed"),  # verify
        ],
    )
    outcome = run_repair_graph(task_obj, ctx_w2, w2_router)
    assert outcome.outcome == "fix_ready"
    assert outcome.summary["lane"] == "full"
    reasons = [e["reason"] for e in outcome.summary["escalations"]]
    assert EscalationReason.FAST_BUDGET_EXHAUSTED.value in reasons


# --- 7. (AL) Lanes Policy Version or ADAPTIVE_LANES Flag Mismatch Across Crash/Resume ---


def test_lanes_policy_or_flag_mismatch_on_resume_forces_full_lane_and_never_downgrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_ENGINE", "graph")
    monkeypatch.setenv("ADAPTIVE_LANES", "on")
    (tmp_path / "paths.py").write_text(
        "def leaf_name(raw_path: str) -> str:\n    return os.path.basename(raw_path)\n",
        encoding="utf-8",
    )

    clock = ClockController()
    store = MemoryStore()
    svc = _make_worker_service(store, clock)
    task_id = "tsk_policy_mismatch"
    _seed_execute_task(
        store,
        clock,
        task_id,
        issue_title="NameError: name 'os' is not defined in paths.py",
        issue_body="leaf_name in paths.py uses os.path.basename without importing os.",
    )
    svc.claim_next("worker_1")

    task_obj = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id=task_id,
        limits=LIMITS,
        attempts_used=0,
        issue={
            "title": "NameError: name 'os' is not defined in paths.py",
            "body": "leaf_name in paths.py uses os.path.basename without importing os.",
        },
        plan={"files": ["paths.py"]},
    )

    fast_proposal = FastEditProposal(
        explanation="Import os in paths.py.",
        edits=[
            {
                "path": "paths.py",
                "old_text": "def leaf_name(raw_path: str) -> str:\n",
                "new_text": "import os\n\n\ndef leaf_name(raw_path: str) -> str:\n",
            }
        ],
    )
    w1_router = ScriptedRouter([fast_proposal])
    ctx_w1 = StoreBackedWorkerContext(
        svc,
        task_id,
        "worker_1",
        tmp_path,
        _oracle_exec_results(),
    )

    # Crash right before fast_review (with fast patch + oracle evidence in checkpoint under al.v1)
    with pytest.raises(RuntimeError, match="Simulated crash before node fast_review"):
        run_repair_graph(task_obj, ctx_w1, w1_router, crash_before_node="fast_review")

    # Simulate code upgrade to lanes_policy_version="al.v2" before resume
    clock.advance(10)
    full_proposal = EditProposal(
        explanation="Full lane repair after policy version upgrade.",
        tests=[
            {
                "path": "test_paths.py",
                "content": "from paths import leaf_name\ndef test_leaf():\n    assert leaf_name('a/b.txt') == 'b.txt'\n",
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
    w2_router = ScriptedRouter([full_proposal, _approved_review("fix: import os in paths.py", "fix-paths")])
    ctx_w2 = StoreBackedWorkerContext(
        svc,
        task_id,
        "worker_1",
        tmp_path,
        [
            _exec_result(0, "1 passed"),  # baseline
            _exec_result(1, "1 failed"),  # reproduce
            _exec_result(0, "2 passed"),  # verify
        ],
    )
    outcome = run_repair_graph(
        task_obj,
        ctx_w2,
        w2_router,
        lanes_policy_version="al.v2",
    )
    assert outcome.outcome == "fix_ready"
    assert outcome.summary["lane"] == "full"
    saved_task = store.get(f"tasks/{task_id}")
    assert saved_task["laneState"]["lane"] == "full"
    assert saved_task["laneState"]["riskFloor"] == "high"


# --- 8. (AL) Stale Oracle Evidence Rejected and Recomputed on Resume ---


def test_stale_oracle_evidence_with_mismatched_diff_hash_is_rejected_and_recomputed_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_ENGINE", "graph")
    monkeypatch.setenv("ADAPTIVE_LANES", "on")
    (tmp_path / "paths.py").write_text(
        "def leaf_name(raw_path: str) -> str:\n    return os.path.basename(raw_path)\n",
        encoding="utf-8",
    )

    clock = ClockController()
    store = MemoryStore()
    svc = _make_worker_service(store, clock)
    task_id = "tsk_stale_oracle"
    _seed_execute_task(
        store,
        clock,
        task_id,
        issue_title="NameError: name 'os' is not defined in paths.py",
        issue_body="leaf_name in paths.py uses os.path.basename without importing os.",
    )
    svc.claim_next("worker_1")

    task_obj = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id=task_id,
        limits=LIMITS,
        attempts_used=0,
        issue={
            "title": "NameError: name 'os' is not defined in paths.py",
            "body": "leaf_name in paths.py uses os.path.basename without importing os.",
        },
        plan={"files": ["paths.py"]},
    )

    fast_proposal = FastEditProposal(
        explanation="Import os in paths.py.",
        edits=[
            {
                "path": "paths.py",
                "old_text": "def leaf_name(raw_path: str) -> str:\n",
                "new_text": "import os\n\n\ndef leaf_name(raw_path: str) -> str:\n",
            }
        ],
    )
    w1_router = ScriptedRouter([fast_proposal])
    ctx_w1 = StoreBackedWorkerContext(
        svc,
        task_id,
        "worker_1",
        tmp_path,
        _oracle_exec_results(),
    )
    with pytest.raises(RuntimeError, match="Simulated crash before node fast_review"):
        run_repair_graph(task_obj, ctx_w1, w1_router, crash_before_node="fast_review")

    # Corrupt the checkpoint's oracle_evidence diffHash to simulate stale oracle evidence from another diff
    checkpointer = StoreRepairCheckpointer(store, task_id=task_id, worker_id="worker_1", clock=clock)
    thread_id = make_thread_id(task_id, "execute")
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    tup = checkpointer.get_tuple(config)
    assert tup is not None
    corrupted_ckpt = copy.deepcopy(tup.checkpoint)
    corrupted_ckpt["channel_values"]["oracle_evidence"]["diffHash"] = "0" * 64
    oracle_ver = corrupted_ckpt["channel_versions"]["oracle_evidence"]
    checkpointer.put(tup.config, corrupted_ckpt, tup.metadata, {"oracle_evidence": oracle_ver})

    # Resume -> fast_review detects diffHash mismatch against rebuilt overlay and recomputes V0/V1 in Docker!
    w2_router = ScriptedRouter([_approved_review("fix: import os in paths.py", "fix-paths")])
    ctx_w2 = StoreBackedWorkerContext(
        svc,
        task_id,
        "worker_1",
        tmp_path,
        _oracle_exec_results(),
    )
    outcome = run_repair_graph(task_obj, ctx_w2, w2_router)
    assert outcome.outcome == "fix_ready"
    assert outcome.summary["lane"] == "fast"
    # Docker V0 + V1 oracle checks WERE re-run because the checkpointed oracle diffHash did not match the rebuilt overlay
    assert len(ctx_w2.commands) == 2


# --- 9. (AL) Checkpoint Size Bound Across Lane Fields and Repeated Escalations ---


def test_checkpoint_size_bound_enforced_across_lane_fields_and_repeated_escalations() -> None:
    triage = TriageReport(
        candidate_lane=LaneName.FAST,
        risk_floor=RiskFloor.LOW,
        localized_files=("src/app.py",),
        undefined_names_by_file={"src/app.py": ("os",)},
        signals=("Missing stdlib import os",),
    )
    state = LaneRuntimeState(
        current_lane=LaneName.FAST,
        risk_floor=RiskFloor.LOW,
        triage=triage,
        fast_started_at=1000.0,
    )

    # Simulate multiple escalations with huge raw candidate diffs and oracle dicts
    huge_diff = "+" + ("x" * 80_000)
    for i in range(5):
        state, dec = escalate(
            state,
            EscalationReason.ORACLE_DISAGREEMENT,
            f"Escalation detail #{i}: " + ("detail " * 100),
            evidence={
                "fastCandidateDiff": huge_diff,
                "triage": triage.to_dict(),
                "oracle": {"summary": "oracle " * 500, "diffHash": f"hash_{i}", "baseSha": BASE_SHA},
            },
            now_wall_ts=1005.0 + i,
        )
        # Compact preservedEvidence stores references/hashes only, never raw diffs or full nested copies
        assert "fastCandidateDiff" not in dec.preserved_evidence
        assert "triage" not in dec.preserved_evidence
        assert "oracle" not in dec.preserved_evidence
        assert dec.preserved_evidence.get("fastCandidateDiffHash") == sha256_hash(huge_diff)

    payload = {
        "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
        "lanes_policy_version": LANES_POLICY_VERSION,
        "lane": state.current_lane.value,
        "risk_floor": state.risk_floor.value,
        "triage_report": state.triage.to_dict(),
        "escalations": [e.to_dict() for e in state.escalations],
        "fast_usage": state.to_fast_usage_dict(1010.0),
    }
    # Compaction passes validation cleanly
    validate_checkpoint_payload(payload)

    # Uncompacted preservedEvidence exceeding MAX_PRESERVED_EVIDENCE_BYTES is rejected deterministically
    bloated_payload = copy.deepcopy(payload)
    bloated_payload["escalations"][0]["preservedEvidence"] = {"rawDump": "y" * (MAX_PRESERVED_EVIDENCE_BYTES + 500)}
    with pytest.raises(ValueError, match="preservedEvidence exceeds"):
        validate_checkpoint_payload(bloated_payload)


# --- 10. (AL) Crash Resumes Counted Separately from EscalationReason and Repair Attempts ---


def test_crash_resumes_counted_separately_from_escalations_and_repair_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_ENGINE", "graph")
    monkeypatch.setenv("ADAPTIVE_LANES", "on")
    (tmp_path / "paths.py").write_text(
        "def leaf_name(raw_path: str) -> str:\n    return os.path.basename(raw_path)\n",
        encoding="utf-8",
    )

    clock = ClockController()
    store = MemoryStore()
    svc = _make_worker_service(store, clock)
    task_id = "tsk_resume_vs_escalation"
    _seed_execute_task(
        store,
        clock,
        task_id,
        issue_title="NameError: name 'os' is not defined in paths.py",
        issue_body="leaf_name in paths.py uses os.path.basename without importing os.",
    )
    svc.claim_next("worker_1")

    task_obj = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id=task_id,
        limits=LIMITS,
        attempts_used=0,
        issue={
            "title": "NameError: name 'os' is not defined in paths.py",
            "body": "leaf_name in paths.py uses os.path.basename without importing os.",
        },
        plan={"files": ["paths.py"]},
    )

    fast_proposal = FastEditProposal(
        explanation="Import os in paths.py.",
        edits=[
            {
                "path": "paths.py",
                "old_text": "def leaf_name(raw_path: str) -> str:\n",
                "new_text": "import os\n\n\ndef leaf_name(raw_path: str) -> str:\n",
            }
        ],
    )
    w1_router = ScriptedRouter([fast_proposal])
    ctx_w1 = StoreBackedWorkerContext(svc, task_id, "worker_1", tmp_path, [])

    with pytest.raises(RuntimeError, match="Simulated crash before node fast_static_oracle"):
        run_repair_graph(task_obj, ctx_w1, w1_router, crash_before_node="fast_static_oracle")

    clock.advance(40)
    reaped = svc.reap_expired()
    assert reaped == [task_id]
    reaped_doc = store.get(f"tasks/{task_id}")
    assert reaped_doc["crashResumes"] == 1
    assert reaped_doc["attempts"]["used"] == 0

    svc.claim_next("worker_2")
    w2_router = ScriptedRouter([_approved_review("fix: import os in paths.py", "fix-paths")])
    ctx_w2 = StoreBackedWorkerContext(
        svc,
        task_id,
        "worker_2",
        tmp_path,
        _oracle_exec_results(),
    )
    outcome = run_repair_graph(task_obj, ctx_w2, w2_router)
    assert outcome.outcome == "fix_ready"
    assert outcome.summary["lane"] == "fast"
    # Zero escalations occurred; crash resume was recorded as a crash_resume event and crashResumes counter
    assert outcome.summary["escalations"] == []
    resume_events = [
        ev for ev in ctx_w2.events if ev[0] == EventType.INFO and ev[2].get("kind") == "crash_resume"
    ]
    assert len(resume_events) == 1
    assert resume_events[0][2]["resumeCount"] == 1
    # Only the single successful repair attempt was charged at completion
    saved_task = store.get(f"tasks/{task_id}")
    assert saved_task["crashResumes"] == 1
    assert saved_task["attempts"]["used"] == 1
