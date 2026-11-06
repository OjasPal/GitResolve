"""Unit and integration tests for the Phase 2A tool-using LangGraph repair engine."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent.graph_engine import (
    CHECKPOINT_SCHEMA_VERSION,
    CheckpointCompatibilityError,
    RepairActionEnvelope,
    SqliteRepairCheckpointer,
    WorkspaceOverlay,
    execute_tool_action,
    make_thread_id,
    reduce_attempts,
    reduce_messages,
    run_repair_graph,
    validate_checkpoint_payload,
)
from app.agent.repair_runner import QualityReview, run_repair
from app.schemas.enums import EventType, FailureCode

BASE_SHA = "b" * 40
LIMITS = {
    "maxRepairAttempts": 3,
    "maxDiffFiles": 5,
    "maxDiffLines": 150,
    "maxModelCalls": 50,
    "taskTimeoutSeconds": 1800,
}


def _exec_result(code: int, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        status="passed" if code == 0 else "failed",
        exit_code=code,
        stdout=stdout,
        stderr=stderr,
        oom_killed=False,
        cleanup_ok=True,
    )


def _approved_review(msg: str = "fix: resolve issue", branch: str = "fix-issue") -> QualityReview:
    return QualityReview(
        commit_message=msg,
        branch_name=branch,
        approved=True,
        requirements=[
            {
                "requirement": "Reported issue is resolved with verified tests.",
                "status": "passed",
                "evidence": "Regression failed before fix and full suite passed after fix.",
            }
        ],
        blocking_issues=[],
    )


class GraphTestContext:
    def __init__(self, root: Path, results: list[Any]) -> None:
        self.root = Path(root)
        self.results = iter(results)
        self.events: list[tuple[Any, str, dict[str, Any]]] = []
        self.stages: list[Any] = []
        self.commands: list[dict[str, Any]] = []
        self.artifacts: dict[str, str] = {}
        self.attempt_count = 0
        self.budgets = {"modelCalls": 0, "inputTokens": 0, "outputTokens": 0}
        self._svc = SimpleNamespace(
            store=SimpleNamespace(
                get=lambda path: (
                    {"budgets": self.budgets.copy()}
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
        return next(self.results)


class ScriptedToolRouter:
    """Deterministic router driving sequences of RepairActionEnvelope actions and QualityReview."""

    def __init__(self, replies: list[Any]) -> None:
        self._replies = iter(replies)
        self.prompts: list[str] = []
        self.calls = 0

    def route_normal(self, prompt: str, **kwargs: Any) -> Any:
        self.prompts.append(prompt)
        self.calls += 1
        item = next(self._replies)
        if callable(item):
            item = item(prompt, kwargs)
        qc = kwargs.get("quality_check")
        res = SimpleNamespace(
            parsed=item if not isinstance(item, str) else None,
            raw_text=item if isinstance(item, str) else (item.model_dump_json() if hasattr(item, "model_dump_json") else ""),
        )
        if qc is not None:
            err = qc(res)
            if err:
                # Simulate ModelRouter quality_check retry with next scripted response
                item2 = next(self._replies)
                res = SimpleNamespace(
                    parsed=item2 if not isinstance(item2, str) else None,
                    raw_text=item2 if isinstance(item2, str) else "",
                )
        return SimpleNamespace(
            result=res,
            usage=SimpleNamespace(input_tokens=120, output_tokens=45),
            selected_provider="google",
            selected_model="gemini-3.8-flash",
        )


# --- 1. Multi-step Tool Loop: CSV Export / NameError ---


def test_multistep_tool_loop_csv_export_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_ENGINE", "graph")
    monkeypatch.setenv("ADAPTIVE_LANES", "off")

    (tmp_path / "exporter.py").write_text(
        "def export_rows(rows):\n"
        "    header = 'id,name\\n'\n"
        "    body = '\\n'.join(f\"{r['id']},{r['name']}\" for r in rows)\n"
        "    return header + missing_newline_helper(body)\n",
        encoding="utf-8",
    )
    (tmp_path / "test_exporter.py").write_text(
        "from exporter import export_rows\n"
        "def test_export_rows():\n"
        "    assert export_rows([{'id': 1, 'name': 'Ada'}]) == 'id,name\\n1,Ada\\n'\n",
        encoding="utf-8",
    )

    actions = [
        RepairActionEnvelope(
            action="read_file",
            args={"path": "exporter.py", "start": 1, "end": 20},
            summary="Inspect exporter.py to see undefined helper.",
        ),
        RepairActionEnvelope(
            action="edit_file",
            args={
                "path": "exporter.py",
                "search": "    return header + missing_newline_helper(body)\n",
                "replace": "    return header + body + ('\\n' if body else '')\n",
            },
            summary="Fix CSV row formatting without undefined helper.",
        ),
        RepairActionEnvelope(
            action="run_tests",
            args={"targets": ["test_exporter.py"]},
            summary="Run exporter unit tests in sandbox.",
        ),
        RepairActionEnvelope(
            action="finish",
            args={"explanation": "Replaced undefined missing_newline_helper with newline suffix."},
            summary="Ready for verification gates.",
        ),
        _approved_review("fix: format CSV rows with trailing newline", "csv-export-newline"),
    ]
    router = ScriptedToolRouter(actions)
    ctx = GraphTestContext(
        tmp_path,
        [
            _exec_result(1, "1 failed: NameError: name 'missing_newline_helper' is not defined"),  # baseline
            _exec_result(0, "1 passed"),  # run_tests tool call
            _exec_result(1, "1 failed"),  # reproduce gate on original
            _exec_result(0, "2 passed"),  # verify gate on patched
        ],
    )
    task = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_csv_tool_loop",
        limits=LIMITS,
        issue={"title": "CSV export NameError", "body": "export_rows in exporter.py fails with NameError."},
        plan={"steps": ["Read exporter.py", "Fix export_rows", "Verify tests"]},
    )

    outcome = run_repair(task, ctx, router)
    assert outcome.outcome == "fix_ready"
    assert "+    return header + body + ('\\n' if body else '')" in outcome.final_diff
    assert "test_gitresolve_regression.py" not in outcome.final_diff
    tool_events = [ev for ev in ctx.events if ev[0] == EventType.TOOL_CALL and ev[2].get("tool")]
    assert [ev[2]["tool"] for ev in tool_events] == ["read_file", "edit_file", "run_tests", "finish"]


# --- 2. Scripted MCQGen Repair ---


def test_multistep_tool_loop_mcqgen_repair_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_ENGINE", "graph")
    (tmp_path / "requirements.txt").write_text("streamlit==1.39.0\n", encoding="utf-8")
    (tmp_path / "quiz_utils.py").write_text(
        "import html\n\n"
        "def render_option(label, text):\n"
        "    return f'<div class=\"opt\">{label}: {text}</div>'\n",
        encoding="utf-8",
    )

    actions = [
        RepairActionEnvelope(
            action="list_tree",
            args={},
            summary="List repository files.",
        ),
        RepairActionEnvelope(
            action="grep",
            args={"pattern": "render_option"},
            summary="Locate render_option implementation.",
        ),
        RepairActionEnvelope(
            action="read_file",
            args={"path": "quiz_utils.py", "start": 1, "end": 20},
            summary="Read quiz_utils.py.",
        ),
        RepairActionEnvelope(
            action="edit_file",
            args={
                "path": "quiz_utils.py",
                "search": "    return f'<div class=\"opt\">{label}: {text}</div>'\n",
                "replace": "    return f'<div class=\"opt\">{html.escape(str(label))}: {html.escape(str(text))}</div>'\n",
            },
            summary="Escape HTML in quiz option rendering.",
        ),
        RepairActionEnvelope(
            action="create_file",
            args={
                "path": "test_quiz_escape.py",
                "content": (
                    "from quiz_utils import render_option\n"
                    "def test_render_option_escapes_html():\n"
                    "    out = render_option('A', '<script>alert(1)</script>')\n"
                    "    assert '<script>' not in out and '&lt;script&gt;' in out\n"
                ),
            },
            summary="Add regression test for HTML escaping in options.",
        ),
        RepairActionEnvelope(
            action="finish",
            args={"explanation": "Escaped label and text in render_option using html.escape."},
            summary="Finish MCQGen patch.",
        ),
        _approved_review("fix: escape HTML entities in quiz options", "mcq-escape-options"),
    ]
    router = ScriptedToolRouter(actions)
    ctx = GraphTestContext(
        tmp_path,
        [
            _exec_result(0, "1 passed"),  # baseline
            _exec_result(1, "1 failed"),  # reproduce
            _exec_result(0, "2 passed"),  # verify
        ],
    )
    task = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_mcqgen_graph",
        limits=LIMITS,
        issue={
            "title": "MCQGen option HTML injection when option text contains angle brackets",
            "body": "render_option in quiz_utils.py does not html.escape option text.",
        },
        plan={"steps": ["Escape option HTML in quiz_utils.py"]},
    )

    outcome = run_repair(task, ctx, router)
    assert outcome.outcome == "fix_ready"
    assert "html.escape" in outcome.final_diff
    assert "test_quiz_escape.py" not in outcome.final_diff


# --- 3. Live Failure Fixture: backup_database Windows Path on Linux ---


def test_backup_database_windows_path_on_linux_succeeds_via_tool_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_ENGINE", "graph")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    filler = "\n".join(f" CONFIG_VAL_{i} = 'the and for with from'\n" for i in range(45))
    (src_dir / "storage.py").write_text(
        "import shutil\n"
        "from pathlib import Path\n\n"
        + filler
        + "\ndef backup_database(db_path: str, backup_dir: str) -> str:\n"
        "    dest = backup_dir + '\\\\backup.db'\n"
        "    shutil.copy2(db_path, dest)\n"
        "    return dest\n",
        encoding="utf-8",
    )

    actions = [
        RepairActionEnvelope(
            action="find_symbol",
            args={"symbol": "backup_database"},
            summary="Locate backup_database in AST.",
        ),
        RepairActionEnvelope(
            action="read_file",
            args={"path": "src/storage.py", "start": 40, "end": 60},
            summary="Read backup_database lines in src/storage.py.",
        ),
        RepairActionEnvelope(
            action="edit_file",
            args={
                "path": "src/storage.py",
                "search": "    dest = backup_dir + '\\\\backup.db'\n    shutil.copy2(db_path, dest)\n    return dest\n",
                "replace": "    dest = Path(backup_dir) / 'backup.db'\n    dest.parent.mkdir(parents=True, exist_ok=True)\n    shutil.copy2(db_path, dest)\n    return str(dest)\n",
            },
            summary="Use pathlib.Path for cross-platform backup destination.",
        ),
        RepairActionEnvelope(
            action="create_file",
            args={
                "path": "test_backup_path.py",
                "content": (
                    "from pathlib import Path\n"
                    "from src.storage import backup_database\n\n"
                    "def test_backup_uses_posix_safe_path(tmp_path):\n"
                    "    db = tmp_path / 'app.db'\n"
                    "    db.write_text('data')\n"
                    "    out = backup_database(str(db), str(tmp_path / 'backups'))\n"
                    "    assert '\\\\backup.db' not in out\n"
                    "    assert Path(out).name == 'backup.db'\n"
                ),
            },
            summary="Create regression test for backup_database path handling.",
        ),
        RepairActionEnvelope(
            action="finish",
            args={"explanation": "Replaced hardcoded backslash path with pathlib.Path in backup_database."},
            summary="Proceed to verification.",
        ),
        _approved_review("fix: use pathlib in backup_database for Linux compatibility", "fix-backup-pathlib"),
    ]
    router = ScriptedToolRouter(actions)
    ctx = GraphTestContext(
        tmp_path,
        [
            _exec_result(0, "1 passed"),  # baseline
            _exec_result(1, "1 failed"),  # reproduce
            _exec_result(0, "2 passed"),  # verify
        ],
    )
    task = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_backup_db_graph",
        limits=LIMITS,
        issue={
            "title": "Backup FileNotFoundError (Windows Path on Linux)",
            "body": "backup_database() in src/storage.py hardcodes backslashes ('\\\\backup.db') instead of pathlib.",
        },
        plan={"steps": ["Update backup_database in src/storage.py to use pathlib.Path"]},
    )

    outcome = run_repair(task, ctx, router)
    assert outcome.outcome == "fix_ready"
    assert "Path(backup_dir) / 'backup.db'" in outcome.final_diff
    assert "\\\\backup.db" in outcome.final_diff


# --- 4. Failed Verification Followed by Materially Different Edit on Next Attempt ---


def test_failed_verification_prompts_materially_different_edit_in_next_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_ENGINE", "graph")
    monkeypatch.setenv("ADAPTIVE_LANES", "off")
    (tmp_path / "calc.py").write_text(
        "def safe_divide(a, b):\n"
        "    return a / b\n",
        encoding="utf-8",
    )

    actions = [
        # Attempt 1: naive fix that returns -1 on zero (fails test expecting 0.0)
        RepairActionEnvelope(
            action="edit_file",
            args={
                "path": "calc.py",
                "search": "    return a / b\n",
                "replace": "    return -1 if b == 0 else a / b\n",
            },
            summary="Return -1 when b is 0.",
        ),
        RepairActionEnvelope(
            action="create_file",
            args={
                "path": "test_calc_zero.py",
                "content": "from calc import safe_divide\ndef test_zero():\n    assert safe_divide(10, 0) == 0.0\n",
            },
            summary="Add zero-division regression test.",
        ),
        RepairActionEnvelope(
            action="finish",
            args={"explanation": "Return -1 on zero."},
            summary="Finish attempt 1.",
        ),
        # Attempt 2: inspects failure feedback and uses 0.0 instead of -1
        RepairActionEnvelope(
            action="edit_file",
            args={
                "path": "calc.py",
                "search": "    return a / b\n",
                "replace": "    return 0.0 if b == 0 else round(a / b, 4)\n",
            },
            summary="Return 0.0 when b is 0.",
        ),
        RepairActionEnvelope(
            action="create_file",
            args={
                "path": "test_calc_zero.py",
                "content": "from calc import safe_divide\ndef test_zero():\n    assert safe_divide(10, 0) == 0.0\n",
            },
            summary="Add zero-division regression test.",
        ),
        RepairActionEnvelope(
            action="finish",
            args={"explanation": "Return 0.0 on zero division and round result."},
            summary="Finish attempt 2.",
        ),
        _approved_review("fix: return 0.0 on zero division in safe_divide", "safe-divide-zero"),
    ]
    router = ScriptedToolRouter(actions)
    ctx = GraphTestContext(
        tmp_path,
        [
            _exec_result(0, "1 passed"),  # baseline
            _exec_result(1, "1 failed: ZeroDivisionError"),  # attempt 1 reproduce
            _exec_result(1, "1 failed: assert -1 == 0.0"),  # attempt 1 verify FAILS
            _exec_result(1, "1 failed: ZeroDivisionError"),  # attempt 2 reproduce
            _exec_result(0, "2 passed"),  # attempt 2 verify PASSES
        ],
    )
    task = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_retry_strategy",
        limits=LIMITS,
        issue={"title": "safe_divide ZeroDivisionError", "body": "safe_divide(a, 0) should return 0.0."},
        plan={},
    )

    outcome = run_repair(task, ctx, router)
    assert outcome.outcome == "fix_ready"
    assert "return 0.0 if b == 0" in outcome.final_diff
    # Attempt 2 prompt must include the concrete failure from Attempt 1
    assert any("assert -1 == 0.0" in p and "Required strategy change" in p for p in router.prompts[3:])


# --- 5. SQLite Checkpoint Crash-and-Resume + Workspace Hash Verification ---


def test_crash_between_nodes_resumes_from_sqlite_checkpoint_without_repeating_patch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_ENGINE", "graph")
    monkeypatch.setenv("ADAPTIVE_LANES", "off")
    db_path = tmp_path / "checkpoints.sqlite"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "math_mod.py").write_text("def double(x):\n    return x + 1\n", encoding="utf-8")

    patch_calls = {"count": 0}

    def edit_step(_prompt: str, _kw: Any) -> RepairActionEnvelope:
        patch_calls["count"] += 1
        return RepairActionEnvelope(
            action="edit_file",
            args={
                "path": "math_mod.py",
                "search": "    return x + 1\n",
                "replace": "    return x * 2\n",
            },
            summary="Multiply by 2.",
        )

    replies = [
        edit_step,
        RepairActionEnvelope(
            action="create_file",
            args={
                "path": "test_double.py",
                "content": "from math_mod import double\ndef test_double():\n    assert double(4) == 8\n",
            },
            summary="Create regression test.",
        ),
        RepairActionEnvelope(
            action="finish",
            args={"explanation": "Fix double(x) to return x * 2."},
            summary="Finish patch.",
        ),
        _approved_review("fix: multiply by 2 in double", "fix-double"),
    ]
    router = ScriptedToolRouter(replies)
    task = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_crash_resume",
        limits=LIMITS,
        issue={"title": "double(x) adds 1 instead of multiplying", "body": "Fix double in math_mod.py"},
        plan={},
    )

    # Run 1: crashes right before `reproduce` after `patch` has committed overlay to SQLite
    cp1 = SqliteRepairCheckpointer(db_path)
    ctx1 = GraphTestContext(repo_dir, [_exec_result(0, "1 passed")])
    with pytest.raises(RuntimeError, match="Simulated crash before node reproduce"):
        run_repair_graph(task, ctx1, router, checkpointer=cp1, crash_before_node="reproduce")

    assert patch_calls["count"] == 1
    thread_id = make_thread_id(task.task_id, "execute")
    saved_tup = cp1.get_tuple({"configurable": {"thread_id": thread_id}})
    assert saved_tup is not None
    saved_vals = saved_tup.checkpoint["channel_values"]
    assert len(saved_vals["overlay_edits"]) == 1
    assert saved_vals["overlay_diff_hash"] != ""
    assert saved_vals["budgets"]["toolCalls"] == 3

    # Run 2: brand-new SqliteRepairCheckpointer on the same db_path resumes from `reproduce`
    cp2 = SqliteRepairCheckpointer(db_path)
    ctx2 = GraphTestContext(
        repo_dir,
        [
            _exec_result(1, "1 failed"),  # reproduce
            _exec_result(0, "2 passed"),  # verify
        ],
    )
    outcome = run_repair_graph(task, ctx2, router, checkpointer=cp2)
    assert outcome.outcome == "fix_ready"
    assert "+    return x * 2" in outcome.final_diff
    # Patch tool calls were NOT re-run during resume!
    assert patch_calls["count"] == 1


# --- 6. Engine Pinning, Schema Version, and Workspace Hash Mismatch Fail Closed ---


def test_engine_swap_or_schema_mismatch_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "pin_test.sqlite"
    cp = SqliteRepairCheckpointer(db_path)
    tid = make_thread_id("tsk_pinned", "execute")
    cp.pin_or_verify_thread(tid, engine_mode="graph", base_sha=BASE_SHA, schema_version=CHECKPOINT_SCHEMA_VERSION)

    # Swapping engine mode to legacy on the same thread raises CheckpointCompatibilityError
    with pytest.raises(CheckpointCompatibilityError, match="Pinned engine mismatch"):
        cp.pin_or_verify_thread(tid, engine_mode="legacy", base_sha=BASE_SHA)

    # Incompatible schema version raises CheckpointCompatibilityError
    with pytest.raises(CheckpointCompatibilityError, match="Incompatible checkpoint schema version"):
        cp.pin_or_verify_thread(tid, engine_mode="graph", base_sha=BASE_SHA, schema_version="2a.v0-old")

    # Calling run_repair with engine="legacy" against a "graph"-pinned checkpointer returns recoverable infra_retry
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "app.py").write_text("x = 1\n", encoding="utf-8")
    ctx = GraphTestContext(repo_dir, [])
    task = SimpleNamespace(base_sha=BASE_SHA, task_id="tsk_pinned", limits=LIMITS, issue={"body": "fix"}, plan={})
    res = run_repair(task, ctx, engine="legacy", checkpointer=cp)
    assert res.outcome == "infra_retry"
    assert res.failure_code == FailureCode.INTERNAL_ERROR


def test_legacy_engine_parity_and_no_graph_interrupt_human_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify AGENT_ENGINE=legacy remains selectable and human gates remain backend states."""
    from tests.unit.test_repair_runner import FIX, run

    monkeypatch.setenv("AGENT_ENGINE", "legacy")
    monkeypatch.setenv("ADAPTIVE_LANES", "off")
    outcome, _ = run(tmp_path, [_exec_result(0, "1 passed"), _exec_result(1, "1 failed"), _exec_result(0, "2 passed")])
    assert outcome.outcome == "fix_ready"
    assert outcome.final_diff == FIX


def test_checkpoint_payload_rejects_forbidden_keys_and_host_paths() -> None:
    with pytest.raises(ValueError, match="forbidden state keys"):
        validate_checkpoint_payload({"checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION, "originals": {"a.py": "x"}})

    with pytest.raises(ValueError, match="unredacted host path"):
        validate_checkpoint_payload({
            "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
            "last_failure_summary": "File at C:\\Users\\Ojas Pal\\AppData\\Local\\Temp\\repo\\a.py failed",
        })

    # Reducers bound length and redact secrets/host paths
    msgs = reduce_messages(
        [],
        [
            {
                "role": "tool",
                "action": f"read_{i}",
                "summary": f"Step {i} at C:\\Users\\Alice\\secret.py",
                "evidence_id": f"ev_{i}",
            }
            for i in range(40)
        ],
    )
    assert len(msgs) == 30
    assert msgs[0]["action"] == "compacted_history"
    assert "ev_0" in msgs[0]["evidence_id"]
    assert "C:\\Users\\Alice" not in msgs[-1]["summary"]

    attempts = reduce_attempts([], [{"attempt": i, "category": "tests", "summary": "fail"} for i in range(20)])
    assert len(attempts) == 12


# --- 7. Tool Security, Schema Validation, and Ambiguous Edit Handling ---


def test_unsafe_and_malformed_tool_calls_rejected_and_counted_against_budgets(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def f():\n    x = 1\n    x = 1\n    return x\n", encoding="utf-8")
    overlay = WorkspaceOverlay(base_sha=BASE_SHA, originals={"app.py": (tmp_path / "app.py").read_text()}, root=tmp_path)
    budgets = {"steps": 0, "toolCalls": 0}

    # 1. Path traversal rejected
    r1 = execute_tool_action(
        {"action": "read_file", "args": {"path": "../secret.txt"}},
        overlay=overlay,
        budgets=budgets,
    )
    assert not r1.ok and "traversal" in r1.summary.lower()
    assert budgets == {"steps": 1, "toolCalls": 1}

    # 2. Secret file (.env) rejected
    r2 = execute_tool_action(
        {"action": "read_file", "args": {"path": ".env"}},
        overlay=overlay,
        budgets=budgets,
    )
    assert not r2.ok and "secret" in r2.summary.lower()
    assert budgets == {"steps": 2, "toolCalls": 2}

    # 3. Protected file (pyproject.toml / requirements.txt) write rejected
    r3 = execute_tool_action(
        {"action": "edit_file", "args": {"path": "pyproject.toml", "search": "a", "replace": "b"}},
        overlay=overlay,
        budgets=budgets,
    )
    assert not r3.ok and "protected" in r3.summary.lower()
    assert budgets == {"steps": 3, "toolCalls": 3}

    # 4. Unsupported extra argument for tool rejected by strict per-tool schema
    r4 = execute_tool_action(
        {"action": "git_diff", "args": {"pattern": "unexpected"}},
        overlay=overlay,
        budgets=budgets,
    )
    assert not r4.ok and "unsupported argument" in r4.summary.lower()
    assert budgets == {"steps": 4, "toolCalls": 4}

    # 5. Ambiguous edit match rejected unless narrowed by line range
    r5 = execute_tool_action(
        {"action": "edit_file", "args": {"path": "app.py", "search": "    x = 1\n", "replace": "    x = 2\n"}},
        overlay=overlay,
        budgets=budgets,
    )
    assert not r5.ok and "ambiguous" in r5.summary.lower()
    assert budgets == {"steps": 5, "toolCalls": 5}

    # 6. Narrowed by start..end line succeeds!
    r6 = execute_tool_action(
        {
            "action": "edit_file",
            "args": {"path": "app.py", "search": "    x = 1\n", "replace": "    x = 2\n", "start": 2, "end": 2},
        },
        overlay=overlay,
        budgets=budgets,
    )
    assert r6.ok
    assert budgets == {"steps": 6, "toolCalls": 6}


# --- 8. Repeated Identical Actions & Budget Exhaustion ---


def test_repeated_identical_tool_actions_trigger_recovery_read_then_stop_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_ENGINE", "graph")
    monkeypatch.setenv("ADAPTIVE_LANES", "off")
    (tmp_path / "service.py").write_text("def run_job():\n    return False\n", encoding="utf-8")

    # Model repeats the exact same grep call 3 times without progress
    repeated_grep = RepairActionEnvelope(
        action="grep",
        args={"pattern": "nonexistent_symbol_xyz"},
        summary="Search for nonexistent symbol.",
    )
    router = ScriptedToolRouter([repeated_grep, repeated_grep, repeated_grep])
    ctx = GraphTestContext(tmp_path, [_exec_result(0, "1 passed")])
    task = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_repeat_guard",
        limits=LIMITS,
        issue={"title": "Bug in service.py", "body": "run_job in service.py returns False."},
        plan={},
    )

    outcome = run_repair(task, ctx, router)
    assert outcome.outcome in {"needs_clarification", "out_of_scope"}
    assert outcome.failure_code != FailureCode.POLICY_VIOLATION
    # Verify automatic recovery read on service.py was triggered before stopping
    tool_events = [ev[2].get("tool") for ev in ctx.events if ev[0] == EventType.TOOL_CALL]
    assert "read_file" in tool_events


def test_tool_call_budget_exhaustion_stops_with_budget_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_ENGINE", "graph")
    monkeypatch.setenv("ADAPTIVE_LANES", "off")
    monkeypatch.setenv("GITRESOLVE_MAX_TOOL_CALLS", "2")
    (tmp_path / "mod.py").write_text("a = 1\n", encoding="utf-8")

    actions = [
        RepairActionEnvelope(action="list_tree", args={}, summary="step 1"),
        RepairActionEnvelope(action="grep", args={"pattern": "a"}, summary="step 2"),
        RepairActionEnvelope(action="read_file", args={"path": "mod.py", "start": 1, "end": 5}, summary="step 3"),
    ]
    router = ScriptedToolRouter(actions)
    ctx = GraphTestContext(tmp_path, [_exec_result(0, "1 passed")])
    task = SimpleNamespace(
        base_sha=BASE_SHA,
        task_id="tsk_budget_cap",
        limits=LIMITS,
        issue={"title": "Fix mod.py", "body": "Update a in mod.py"},
        plan={},
    )

    outcome = run_repair(task, ctx, router)
    assert outcome.outcome == "failed"
    assert outcome.failure_code == FailureCode.BUDGET_EXCEEDED
