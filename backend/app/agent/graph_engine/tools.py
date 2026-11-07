"""Typed tool registry and flat Gemini-compatible action envelope for Phase 2A."""
from __future__ import annotations

import ast
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.graph_engine.workspace import WorkspaceOverlay, validate_safe_repo_path
from app.core.redaction import redact
from app.schemas.enums import EventType

ActionName = Literal[
    "list_tree",
    "grep",
    "read_file",
    "find_symbol",
    "find_references",
    "run_tests",
    "run_check",
    "run_snippet",
    "edit_file",
    "create_file",
    "git_diff",
    "ask_user",
    "report_blocker",
    "finish",
]


class RepairActionArgs(BaseModel):
    """Flat optional arguments object accepted by Google GenAI response_schema."""

    model_config = ConfigDict(extra="forbid")

    path: str | None = Field(default=None, max_length=240)
    start: int | None = Field(default=None, ge=1, le=20_000)
    end: int | None = Field(default=None, ge=1, le=20_000)
    pattern: str | None = Field(default=None, max_length=300)
    symbol: str | None = Field(default=None, max_length=160)
    search: str | None = Field(default=None, max_length=12_000)
    replace: str | None = Field(default=None, max_length=12_000)
    content: str | None = Field(default=None, max_length=24_000)
    targets: list[str] | None = Field(default=None, max_length=8)
    module: str | None = Field(default=None, max_length=160)
    question: str | None = Field(default=None, max_length=600)
    reason: str | None = Field(default=None, max_length=600)
    explanation: str | None = Field(default=None, max_length=1000)


class RepairActionEnvelope(BaseModel):
    """Gemini-validated flat action enum + args envelope."""

    model_config = ConfigDict(extra="forbid")

    action: ActionName
    args: RepairActionArgs = Field(default_factory=RepairActionArgs)
    summary: str = Field(default="", max_length=500)


# --- Per-tool strict validation schemas (extra="forbid") ---


class ListTreeToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str | None = Field(default=None, max_length=240)


class GrepToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pattern: str = Field(min_length=1, max_length=300)
    path: str | None = Field(default=None, max_length=240)


class ReadFileToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=240)
    start: int = Field(default=1, ge=1, le=20_000)
    end: int = Field(default=120, ge=1, le=20_000)


class FindSymbolToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=160)
    path: str | None = Field(default=None, max_length=240)


class FindReferencesToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=160)
    path: str | None = Field(default=None, max_length=240)


class RunTestsToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    targets: list[str] = Field(default_factory=list, max_length=8)


class RunCheckToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    targets: list[str] = Field(default_factory=list, max_length=8)
    path: str | None = Field(default=None, max_length=240)


class RunSnippetToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    module: str | None = Field(default=None, max_length=160)
    path: str | None = Field(default=None, max_length=240)


class EditFileToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=240)
    search: str = Field(default="", max_length=12_000)
    replace: str = Field(min_length=0, max_length=12_000)
    start: int | None = Field(default=None, ge=1, le=20_000)
    end: int | None = Field(default=None, ge=1, le=20_000)


class CreateFileToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=24_000)


class GitDiffToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AskUserToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=3, max_length=600)


class ReportBlockerToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=600)
    path: str | None = Field(default=None, max_length=240)


class FinishToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    explanation: str = Field(default="Completed requested edits and ready for verification.", max_length=1000)


_STRICT_TOOL_MODELS: dict[str, tuple[type[BaseModel], tuple[str, ...]]] = {
    "list_tree": (ListTreeToolArgs, ("path",)),
    "grep": (GrepToolArgs, ("pattern", "path")),
    "read_file": (ReadFileToolArgs, ("path", "start", "end")),
    "find_symbol": (FindSymbolToolArgs, ("symbol", "path")),
    "find_references": (FindReferencesToolArgs, ("symbol", "path")),
    "run_tests": (RunTestsToolArgs, ("targets",)),
    "run_check": (RunCheckToolArgs, ("targets", "path")),
    "run_snippet": (RunSnippetToolArgs, ("module", "path")),
    "edit_file": (EditFileToolArgs, ("path", "search", "replace", "start", "end")),
    "create_file": (CreateFileToolArgs, ("path", "content")),
    "git_diff": (GitDiffToolArgs, ()),
    "ask_user": (AskUserToolArgs, ("question",)),
    "report_blocker": (ReportBlockerToolArgs, ("reason", "path")),
    "finish": (FinishToolArgs, ("explanation",)),
}

_SNIPPET_CHECKER = (
    "import ast,importlib,pathlib,sys\n"
    "sys.path.insert(0,'.')\n"
    "t=sys.argv[1];m=sys.argv[2]\n"
    "if t:\n"
    " s=pathlib.Path(t).read_text('utf-8')\n"
    " ast.parse(s,t);compile(s,t,'exec')\n"
    "if m:\n"
    " importlib.import_module(m)\n"
    "print('SNIPPET_CHECK_OK')\n"
)


@dataclass
class ToolExecutionResult:
    ok: bool
    action: str
    summary: str
    output: str
    action_hash: str
    evidence_id: str = ""
    path: str = ""
    duration_ms: float = 0.0
    terminal_signal: Literal["", "finish", "ask_user", "blocker"] = ""
    recovered_context: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def compute_action_hash(action: str, args_dict: dict[str, Any]) -> str:
    canonical = json.dumps({"action": action, "args": args_dict}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _emit_tool_event(
    ctx: Any,
    *,
    action: str,
    path: str,
    ok: bool,
    duration_ms: float,
    attempt: int,
    summary: str,
) -> None:
    if ctx is None or not hasattr(ctx, "event"):
        return
    safe_summary = redact(summary, 240)
    safe_path = redact(path, 120) if path else ""
    msg = f"Tool {action}" + (f" ({safe_path})" if safe_path else "") + f": {safe_summary}"
    payload = {
        "tool": action,
        "path": safe_path,
        "ok": ok,
        "durationMs": round(duration_ms, 3),
        "attempt": attempt,
        "summary": safe_summary,
    }
    try:
        ctx.event(EventType.TOOL_CALL, msg, data=payload)
    except TypeError:
        ctx.event(EventType.TOOL_CALL, msg)


def validate_tool_envelope(raw: RepairActionEnvelope | dict[str, Any] | str) -> tuple[RepairActionEnvelope | None, BaseModel | None, str]:
    """Validate both the outer flat action envelope and the strict per-tool args schema."""
    try:
        if isinstance(raw, RepairActionEnvelope):
            envelope = raw
        elif isinstance(raw, str):
            envelope = RepairActionEnvelope.model_validate_json(raw)
        elif isinstance(raw, dict):
            envelope = RepairActionEnvelope.model_validate(raw)
        else:
            return None, None, f"Unsupported action payload type: {type(raw).__name__}"
    except ValidationError as exc:
        return None, None, f"Invalid tool action envelope: {redact(str(exc), 400)}"

    model_cls, allowed_keys = _STRICT_TOOL_MODELS[envelope.action]
    raw_args = envelope.args.model_dump(exclude_none=True)
    unexpected = set(raw_args.keys()) - set(allowed_keys)
    if unexpected:
        return envelope, None, f"Tool '{envelope.action}' received unsupported argument(s): {sorted(unexpected)}"
    try:
        strict_args = model_cls.model_validate(raw_args)
    except ValidationError as exc:
        return envelope, None, f"Invalid arguments for tool '{envelope.action}': {redact(str(exc), 400)}"
    return envelope, strict_args, ""


def execute_tool_action(
    raw: RepairActionEnvelope | dict[str, Any] | str,
    *,
    overlay: WorkspaceOverlay,
    ctx: Any = None,
    base_sha: str = "",
    default_test_command: list[str] | None = None,
    attempt: int = 1,
    budgets: dict[str, int] | None = None,
) -> ToolExecutionResult:
    """Validate and execute a single typed tool action against the workspace overlay or sandbox."""
    started = time.perf_counter()
    if budgets is not None:
        budgets["steps"] = budgets.get("steps", 0) + 1
        budgets["toolCalls"] = budgets.get("toolCalls", 0) + 1

    envelope, strict_args, err = validate_tool_envelope(raw)
    if err or envelope is None or strict_args is None:
        act_name = envelope.action if envelope is not None else "invalid_action"
        dur = (time.perf_counter() - started) * 1000.0
        _emit_tool_event(
            ctx,
            action=act_name,
            path="",
            ok=False,
            duration_ms=dur,
            attempt=attempt,
            summary=err or "Schema validation failed.",
        )
        return ToolExecutionResult(
            ok=False,
            action=act_name,
            summary=err or "Schema validation failed.",
            output=err or "Schema validation failed.",
            action_hash=compute_action_hash(act_name, {"error": err[:80]}),
            duration_ms=dur,
        )

    action = envelope.action
    args_dict = strict_args.model_dump(exclude_none=True)
    act_hash = compute_action_hash(action, args_dict)
    ev_id = f"{action}:{act_hash[:8]}"
    files = overlay.current_files()

    if action == "list_tree":
        t_args: ListTreeToolArgs = strict_args  # type: ignore[assignment]
        prefix = ""
        if t_args.path:
            ok, norm, perr = validate_safe_repo_path(t_args.path, root=overlay.root, for_write=False)
            if not ok:
                return _finalize(ctx, started, False, action, t_args.path, perr, perr, act_hash, ev_id, attempt)
            prefix = norm.rstrip("/")
        matched = [
            f for f in sorted(files)
            if not prefix or f == prefix or f.startswith(prefix + "/")
        ]
        out = "\n".join(matched[:200]) if matched else "(no matching files)"
        summary = f"Listed {len(matched)} repository file(s)."
        return _finalize(ctx, started, True, action, prefix, summary, out, act_hash, ev_id, attempt)

    if action == "grep":
        g_args: GrepToolArgs = strict_args  # type: ignore[assignment]
        scope_prefix = ""
        if g_args.path:
            ok, norm, perr = validate_safe_repo_path(g_args.path, root=overlay.root, for_write=False)
            if not ok:
                return _finalize(ctx, started, False, action, g_args.path, perr, perr, act_hash, ev_id, attempt)
            scope_prefix = norm
        try:
            regex = re.compile(g_args.pattern)
        except re.error:
            regex = re.compile(re.escape(g_args.pattern))
        hits: list[str] = []
        for fname in sorted(files):
            if scope_prefix and fname != scope_prefix and not fname.startswith(scope_prefix.rstrip("/") + "/"):
                continue
            for idx, line in enumerate(files[fname].splitlines(), start=1):
                if regex.search(line):
                    hits.append(f"{fname}:{idx}: {redact(line.strip(), 180)}")
                    if len(hits) >= 60:
                        break
            if len(hits) >= 60:
                break
        out = "\n".join(hits) if hits else f"No matches for pattern {g_args.pattern!r}."
        summary = f"Found {len(hits)} match(es) for {g_args.pattern!r}."
        return _finalize(ctx, started, True, action, scope_prefix, summary, out, act_hash, ev_id, attempt)

    if action == "read_file":
        r_args: ReadFileToolArgs = strict_args  # type: ignore[assignment]
        ok, content, perr = overlay.read_file_content(r_args.path)
        if not ok:
            return _finalize(ctx, started, False, action, r_args.path, perr, perr, act_hash, ev_id, attempt)
        lines = content.splitlines()
        start_line = max(1, min(r_args.start, max(1, len(lines))))
        end_line = max(start_line, min(r_args.end, start_line + 249, len(lines)))
        numbered = "\n".join(
            f"{ln_no:4}: {redact(lines[ln_no - 1], 240)}"
            for ln_no in range(start_line, end_line + 1)
        )
        header = f"--- {r_args.path} (lines {start_line}..{end_line} of {len(lines)}) ---\n"
        summary = f"Read {r_args.path} lines {start_line}..{end_line} ({len(lines)} total)."
        return _finalize(
            ctx,
            started,
            True,
            action,
            r_args.path,
            summary,
            header + numbered,
            act_hash,
            ev_id,
            attempt,
            extra={"read_path": r_args.path},
        )

    if action == "find_symbol":
        fs_args: FindSymbolToolArgs = strict_args  # type: ignore[assignment]
        scope_file = ""
        if fs_args.path:
            ok, norm, perr = validate_safe_repo_path(fs_args.path, root=overlay.root, for_write=False)
            if not ok:
                return _finalize(ctx, started, False, action, fs_args.path, perr, perr, act_hash, ev_id, attempt)
            scope_file = norm
        defs: list[str] = []
        matched_paths: list[str] = []
        for fname in sorted(files):
            if not fname.endswith(".py"):
                continue
            if scope_file and fname != scope_file:
                continue
            src = files[fname]
            try:
                tree = ast.parse(src, filename=fname)
            except SyntaxError:
                continue
            src_lines = src.splitlines()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == fs_args.symbol:
                    s_ln = getattr(node, "lineno", 1)
                    e_ln = min(len(src_lines), getattr(node, "end_lineno", s_ln + 25) or (s_ln + 25))
                    snippet = "\n".join(
                        f"{i:4}: {redact(src_lines[i - 1], 220)}"
                        for i in range(s_ln, min(e_ln + 1, s_ln + 45))
                    )
                    defs.append(f"{fname}:{s_ln}..{e_ln} ({type(node).__name__} {node.name}):\n{snippet}")
                    matched_paths.append(fname)
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == fs_args.symbol:
                            s_ln = getattr(node, "lineno", 1)
                            defs.append(f"{fname}:{s_ln} (Assign {target.id}): {redact(src_lines[s_ln - 1], 200)}")
                            matched_paths.append(fname)
        out = "\n\n".join(defs[:15]) if defs else f"Symbol {fs_args.symbol!r} not found in Python AST."
        summary = f"Found {len(defs)} AST definition(s) for {fs_args.symbol!r}."
        return _finalize(
            ctx,
            started,
            True,
            action,
            matched_paths[0] if matched_paths else scope_file,
            summary,
            out,
            act_hash,
            ev_id,
            attempt,
            extra={"matched_paths": matched_paths},
        )

    if action == "find_references":
        fr_args: FindReferencesToolArgs = strict_args  # type: ignore[assignment]
        refs: list[str] = []
        dynamic_seen = False
        for fname in sorted(files):
            if not fname.endswith(".py"):
                continue
            src = files[fname]
            try:
                tree = ast.parse(src, filename=fname)
            except SyntaxError:
                continue
            src_lines = src.splitlines()
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"getattr", "eval", "__import__"}:
                    dynamic_seen = True
                if isinstance(node, ast.Name) and node.id == fr_args.symbol:
                    ln = getattr(node, "lineno", 1)
                    refs.append(f"{fname}:{ln}: {redact(src_lines[ln - 1].strip(), 180)}")
                elif isinstance(node, ast.Attribute) and node.attr == fr_args.symbol:
                    ln = getattr(node, "lineno", 1)
                    refs.append(f"{fname}:{ln}: {redact(src_lines[ln - 1].strip(), 180)}")
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name == fr_args.symbol or alias.asname == fr_args.symbol:
                            ln = getattr(node, "lineno", 1)
                            refs.append(f"{fname}:{ln}: {redact(src_lines[ln - 1].strip(), 180)}")
        dedup_refs = list(dict.fromkeys(refs))[:50]
        dyn_note = f"\n[dynamic_uncertainty={str(dynamic_seen).lower()}]"
        out = ("\n".join(dedup_refs) if dedup_refs else f"No static AST references to {fr_args.symbol!r}.") + dyn_note
        summary = f"Found {len(dedup_refs)} reference(s) to {fr_args.symbol!r} (dynamic_uncertainty={dynamic_seen})."
        return _finalize(ctx, started, True, action, fr_args.path or "", summary, out, act_hash, ev_id, attempt)

    if action == "edit_file":
        e_args: EditFileToolArgs = strict_args  # type: ignore[assignment]
        ok, msg = overlay.apply_edit(
            e_args.path,
            old_text=e_args.search,
            new_text=e_args.replace,
            start_line=e_args.start,
            end_line=e_args.end,
        )
        return _finalize(ctx, started, ok, action, e_args.path, msg, msg, act_hash, ev_id, attempt)

    if action == "create_file":
        c_args: CreateFileToolArgs = strict_args  # type: ignore[assignment]
        ok, msg = overlay.create_file(c_args.path, c_args.content)
        return _finalize(ctx, started, ok, action, c_args.path, msg, msg, act_hash, ev_id, attempt)

    if action == "git_diff":
        diff_text = overlay.combined_diff()
        summary = (
            f"Generated overlay diff ({len(diff_text.splitlines())} lines)."
            if diff_text
            else "Workspace overlay has no edits yet."
        )
        return _finalize(
            ctx,
            started,
            True,
            action,
            "",
            summary,
            redact(diff_text, 4000) if diff_text else "(no changes)",
            act_hash,
            ev_id,
            attempt,
        )

    if action == "run_tests":
        rt_args: RunTestsToolArgs = strict_args  # type: ignore[assignment]
        if ctx is None or not hasattr(ctx, "execute"):
            msg = "Sandbox execution context is required for run_tests."
            return _finalize(ctx, started, False, action, "", msg, msg, act_hash, ev_id, attempt)
        cmd = list(default_test_command or ["python", "-m", "pytest", "-q", "--tb=short"])
        valid_targets: list[str] = []
        for target in rt_args.targets:
            ok, norm, perr = validate_safe_repo_path(target, root=overlay.root, for_write=False)
            if not ok:
                return _finalize(ctx, started, False, action, target, perr, perr, act_hash, ev_id, attempt)
            valid_targets.append(norm)
        v2_info: dict[str, Any] = {}
        if not valid_targets:
            from app.agent.lanes.import_graph import select_tests_v2
            changed_paths = [
                str(rec.get("path") or "")
                for rec in overlay.serialize_edits()
                if rec.get("path")
            ]
            v2_res = select_tests_v2(files, changed_paths)
            v2_info = v2_res.to_dict()
            if v2_res.used_v2 and v2_res.selected_tests:
                valid_targets = list(v2_res.selected_tests[:8])
        if valid_targets:
            cmd = [*cmd, "--", *valid_targets]
        res = ctx.execute(
            commit_sha=base_sha or overlay.base_sha,
            command=cmd,
            patch=overlay.combined_diff() or None,
        )
        combined_out = redact(((res.stdout or "") + "\n" + (res.stderr or "")).strip()[-2400:], 2400)
        ok_run = res.status == "passed" and res.exit_code == 0 and not res.oom_killed and res.cleanup_ok
        summary = f"Sandbox pytest exit_code={res.exit_code} status={res.status}."
        extra_meta: dict[str, Any] = {"exit_code": res.exit_code, "status": res.status}
        if v2_info:
            extra_meta["v2Selection"] = v2_info
        return _finalize(
            ctx,
            started,
            ok_run,
            action,
            ",".join(valid_targets),
            summary,
            combined_out or summary,
            act_hash,
            ev_id,
            attempt,
            extra=extra_meta,
        )

    if action == "run_check":
        rc_args: RunCheckToolArgs = strict_args  # type: ignore[assignment]
        if ctx is None or not hasattr(ctx, "execute"):
            msg = "Sandbox execution context is required for run_check."
            return _finalize(ctx, started, False, action, "", msg, msg, act_hash, ev_id, attempt)
        raw_targets = list(rc_args.targets)
        if rc_args.path:
            raw_targets.append(rc_args.path)
        if not raw_targets:
            raw_targets = [
                rec["path"] for rec in overlay.serialize_edits() if str(rec.get("path", "")).endswith(".py")
            ] or [f for f in sorted(files) if f.endswith(".py")][:6]
        checked_targets: list[str] = []
        for t in raw_targets[:8]:
            ok, norm, perr = validate_safe_repo_path(t, root=overlay.root, for_write=False)
            if not ok:
                return _finalize(ctx, started, False, action, t, perr, perr, act_hash, ev_id, attempt)
            checked_targets.append(norm)
        cmd = ["python", "-m", "py_compile", *checked_targets]
        res = ctx.execute(
            commit_sha=base_sha or overlay.base_sha,
            command=cmd,
            patch=overlay.combined_diff() or None,
        )
        ok_run = res.status == "passed" and res.exit_code == 0 and not res.oom_killed and res.cleanup_ok
        combined_out = redact(((res.stdout or "") + "\n" + (res.stderr or "")).strip()[-1600:], 1600)
        summary = f"py_compile check {'passed' if ok_run else 'failed'} on {checked_targets}."
        return _finalize(ctx, started, ok_run, action, ",".join(checked_targets), summary, combined_out or summary, act_hash, ev_id, attempt)

    if action == "run_snippet":
        rs_args: RunSnippetToolArgs = strict_args  # type: ignore[assignment]
        if ctx is None or not hasattr(ctx, "execute"):
            msg = "Sandbox execution context is required for run_snippet."
            return _finalize(ctx, started, False, action, "", msg, msg, act_hash, ev_id, attempt)
        target_path = ""
        if rs_args.path:
            ok, norm, perr = validate_safe_repo_path(rs_args.path, root=overlay.root, for_write=False)
            if not ok:
                return _finalize(ctx, started, False, action, rs_args.path, perr, perr, act_hash, ev_id, attempt)
            target_path = norm
        module_name = ""
        if rs_args.module:
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_.]*$", rs_args.module):
                msg = f"Invalid Python module identifier for run_snippet: {rs_args.module!r}"
                return _finalize(ctx, started, False, action, "", msg, msg, act_hash, ev_id, attempt)
            module_name = rs_args.module
        cmd = ["python", "-I", "-c", _SNIPPET_CHECKER, target_path, module_name]
        res = ctx.execute(
            commit_sha=base_sha or overlay.base_sha,
            command=cmd,
            patch=overlay.combined_diff() or None,
        )
        ok_run = res.status == "passed" and res.exit_code == 0 and "SNIPPET_CHECK_OK" in (res.stdout or "")
        combined_out = redact(((res.stdout or "") + "\n" + (res.stderr or "")).strip()[-1600:], 1600)
        summary = f"Isolated snippet check {'passed' if ok_run else 'failed'} ({target_path or module_name})."
        return _finalize(ctx, started, ok_run, action, target_path or module_name, summary, combined_out or summary, act_hash, ev_id, attempt)

    if action == "ask_user":
        au_args: AskUserToolArgs = strict_args  # type: ignore[assignment]
        q_clean = redact(au_args.question, 400)
        summary = f"Clarification requested: {q_clean}"
        return _finalize(
            ctx,
            started,
            True,
            action,
            "",
            summary,
            summary,
            act_hash,
            ev_id,
            attempt,
            terminal_signal="ask_user",
            extra={"question": q_clean},
        )

    if action == "report_blocker":
        rb_args: ReportBlockerToolArgs = strict_args  # type: ignore[assignment]
        reason_clean = redact(rb_args.reason, 400)
        # Check if the blocker is actually a recoverable context request referencing a repository file/symbol
        candidate_files: list[str] = []
        if rb_args.path and rb_args.path in files:
            candidate_files.append(rb_args.path)
        for fname in sorted(files):
            base = PurePosixPath(fname).name
            if fname in reason_clean or (len(base) > 3 and base in reason_clean):
                if fname not in candidate_files:
                    candidate_files.append(fname)
        if candidate_files:
            target = candidate_files[0]
            lines = files[target].splitlines()
            numbered = "\n".join(
                f"{i + 1:4}: {redact(lines[i], 220)}"
                for i in range(min(len(lines), 180))
            )
            recovered = (
                f"Recovered context for {target} (blocker converted to read_file):\n"
                + numbered
            )
            summary = f"Recovered context for {target} instead of stopping on blocker."
            return _finalize(
                ctx,
                started,
                True,
                action,
                target,
                summary,
                recovered,
                act_hash,
                ev_id,
                attempt,
                recovered_context=True,
                extra={"recovered_path": target},
            )
        summary = f"Reported blocker: {reason_clean}"
        return _finalize(
            ctx,
            started,
            False,
            action,
            rb_args.path or "",
            summary,
            summary,
            act_hash,
            ev_id,
            attempt,
            terminal_signal="blocker",
            extra={"reason": reason_clean},
        )

    # finish
    fin_args: FinishToolArgs = strict_args  # type: ignore[assignment]
    expl = redact(fin_args.explanation, 600)
    summary = f"Model requested transition to verification gates: {expl[:180]}"
    return _finalize(
        ctx,
        started,
        True,
        action,
        "",
        summary,
        expl,
        act_hash,
        ev_id,
        attempt,
        terminal_signal="finish",
        extra={"explanation": expl},
    )


def _finalize(
    ctx: Any,
    started: float,
    ok: bool,
    action: str,
    path: str,
    summary: str,
    output: str,
    action_hash: str,
    evidence_id: str,
    attempt: int,
    *,
    terminal_signal: Literal["", "finish", "ask_user", "blocker"] = "",
    recovered_context: bool = False,
    extra: dict[str, Any] | None = None,
) -> ToolExecutionResult:
    dur = (time.perf_counter() - started) * 1000.0
    _emit_tool_event(
        ctx,
        action=action,
        path=path,
        ok=ok,
        duration_ms=dur,
        attempt=attempt,
        summary=summary,
    )
    return ToolExecutionResult(
        ok=ok,
        action=action,
        summary=redact(summary, 400),
        output=redact(output, 2800),
        action_hash=action_hash,
        evidence_id=evidence_id,
        path=path,
        duration_ms=dur,
        terminal_signal=terminal_signal,
        recovered_context=recovered_context,
        extra=extra or {},
    )
