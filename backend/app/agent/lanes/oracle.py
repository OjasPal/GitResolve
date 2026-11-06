"""V0 static AST/compile oracle and V1 isolated import/smoke verifier for Adaptive Lanes."""
from __future__ import annotations

from typing import Any

from app.agent.lanes.planner import apply_unified_diff
from app.agent.lanes.triage import analyze_python_source
from app.agent.lanes.types import (
    OracleEvidence,
    VerificationLevel,
    VerificationPlan,
)
from app.services.ids import sha256_hash

ORACLE_GATE_MARKER = "STATIC ORACLE GATE PASSED: V0=ok V1=ok"
ORACLE_ORIG_MARKER = "ORACLE_V0_ORIG_FAIL"

_ORIG_CHECKER = (
    "import ast,sys;f,u,r=sys.argv[1:4];s=open(f).read();t=ast.parse(s,f);compile(s,f,'exec');"
    "I={a.asname or a.name.split('.')[0]for n in t.body if isinstance(n,(ast.Import,ast.ImportFrom))for a in n.names};"
    "L={n.id for n in ast.walk(t)if isinstance(n,ast.Name)and isinstance(n.ctx,ast.Load)};"
    "ok=all(x in L-I for x in u.split(',')if x)and all(x in I-L for x in r.split(',')if x);"
    "print('ORACLE_V0_ORIG_FAIL'if ok else'ORACLE_V0_NO_DEFECT');sys.exit(1 if ok else 0)"
)

_PATCH_CHECKER = (
    "import ast,importlib.util as iu,sys;f,u,r=sys.argv[1:4];s=open(f).read();t=ast.parse(s,f);compile(s,f,'exec');"
    "I={a.asname or a.name.split('.')[0]for n in t.body if isinstance(n,(ast.Import,ast.ImportFrom))for a in n.names};"
    "assert all(x in I for x in u.split(',')if x)and not(I&set(r.split(','))),'V0';"
    "sys.path.insert(0,'.');sp=iu.spec_from_file_location('_v1',f);m=iu.module_from_spec(sp);sp.loader.exec_module(m);"
    "print('STATIC ORACLE GATE PASSED: V0=ok V1=ok')"
)


def build_oracle_command(
    mode: str,
    target_file: str,
    undefined_names: tuple[str, ...],
    removed_unused_names: tuple[str, ...],
) -> list[str]:
    if mode not in {"orig", "patch"}:
        raise ValueError(f"Unsupported oracle mode: {mode}")
    script = _ORIG_CHECKER if mode == "orig" else _PATCH_CHECKER
    undef_csv = ",".join(undefined_names)
    unused_csv = ",".join(removed_unused_names)
    return ["python", "-I", "-c", script, target_file, undef_csv, unused_csv]


def run_oracle_verification(
    ctx: Any,
    base_sha: str,
    originals: dict[str, str],
    publishable_diff: str,
    plan: VerificationPlan,
) -> OracleEvidence:
    """Run V0 + V1 oracle checks on original and patched snapshots in fresh Docker containers.

    Never trusts model self-attestation; requires sandbox-produced markers and exit codes.
    """
    facts = plan.patch_facts
    diff_hash = sha256_hash(publishable_diff)
    if len(facts.touched_files) != 1 or not facts.non_import_ast_unchanged:
        return OracleEvidence(
            level=VerificationLevel.V0,
            original_failed=False,
            patched_passed=False,
            v0_passed=False,
            v1_passed=False,
            base_sha=base_sha,
            diff_hash=diff_hash,
            summary="Oracle ineligible: patch touches multiple files or alters non-import AST.",
            valid_for_fast_lane=False,
        )

    target_file = facts.touched_files[0]
    orig_src = originals.get(target_file, "")
    patched_map = apply_unified_diff(originals, publishable_diff)
    patch_src = patched_map.get(target_file, "")

    orig_analysis = analyze_python_source(orig_src, target_file)
    patch_analysis = analyze_python_source(patch_src, target_file)

    removed_bound = tuple(item.split(":", 1)[-1] for item in facts.removed_imports if ":" in item)
    resolved_undef = facts.resolved_undefined_names

    if (
        not orig_analysis.syntax_valid
        or not patch_analysis.syntax_valid
        or patch_analysis.undefined_names
        or orig_analysis.non_import_ast_dump != patch_analysis.non_import_ast_dump
        or (not resolved_undef and not removed_bound)
    ):
        return OracleEvidence(
            level=VerificationLevel.V0,
            original_failed=False,
            patched_passed=False,
            v0_passed=False,
            v1_passed=False,
            original_undefined_names=orig_analysis.undefined_names,
            patched_undefined_names=patch_analysis.undefined_names,
            removed_unused_imports=removed_bound,
            base_sha=base_sha,
            diff_hash=diff_hash,
            summary="Static AST delta check failed before container execution.",
            valid_for_fast_lane=False,
        )

    orig_cmd = build_oracle_command("orig", target_file, resolved_undef, removed_bound)
    patch_cmd = build_oracle_command("patch", target_file, resolved_undef, removed_bound)

    orig_res = ctx.execute(commit_sha=base_sha, command=orig_cmd, patch="")
    orig_stdout = getattr(orig_res, "stdout", "") or ""
    orig_failed = (
        getattr(orig_res, "exit_code", None) == 1
        and getattr(orig_res, "cleanup_ok", False) is True
        and not getattr(orig_res, "oom_killed", False)
        and ORACLE_ORIG_MARKER in orig_stdout
    )
    exec_ids: list[str] = []
    if getattr(orig_res, "execution_id", None):
        exec_ids.append(orig_res.execution_id)

    if not orig_failed:
        return OracleEvidence(
            level=VerificationLevel.V0,
            original_failed=False,
            patched_passed=False,
            v0_passed=False,
            v1_passed=False,
            original_undefined_names=orig_analysis.undefined_names,
            patched_undefined_names=patch_analysis.undefined_names,
            removed_unused_imports=removed_bound,
            execution_ids=tuple(exec_ids),
            base_sha=base_sha,
            diff_hash=diff_hash,
            summary="Original snapshot did not fail the V0 static oracle in Docker.",
            valid_for_fast_lane=False,
        )

    ctx.verify_static_oracle = True
    ctx.oracle_original_failed = True
    try:
        patch_res = ctx.execute(commit_sha=base_sha, command=patch_cmd, patch=publishable_diff)
    finally:
        ctx.verify_static_oracle = False
        ctx.oracle_original_failed = False

    if getattr(patch_res, "execution_id", None):
        exec_ids.append(patch_res.execution_id)

    patch_stdout = getattr(patch_res, "stdout", "") or ""
    patched_ok = (
        getattr(patch_res, "status", "") == "passed"
        and getattr(patch_res, "exit_code", None) == 0
        and getattr(patch_res, "cleanup_ok", False) is True
        and not getattr(patch_res, "oom_killed", False)
        and ORACLE_GATE_MARKER in patch_stdout
    )

    if not patched_ok:
        return OracleEvidence(
            level=VerificationLevel.V1,
            original_failed=True,
            patched_passed=False,
            v0_passed=False,
            v1_passed=False,
            original_undefined_names=orig_analysis.undefined_names,
            patched_undefined_names=patch_analysis.undefined_names,
            removed_unused_imports=removed_bound,
            imported_modules=(target_file,),
            execution_ids=tuple(exec_ids),
            base_sha=base_sha,
            diff_hash=diff_hash,
            summary="Patched snapshot failed V0/V1 static or import verification in Docker.",
            valid_for_fast_lane=False,
        )

    return OracleEvidence(
        level=VerificationLevel.V1,
        original_failed=True,
        patched_passed=True,
        v0_passed=True,
        v1_passed=True,
        original_undefined_names=orig_analysis.undefined_names,
        patched_undefined_names=patch_analysis.undefined_names,
        removed_unused_imports=removed_bound,
        imported_modules=(target_file,),
        execution_ids=tuple(exec_ids),
        base_sha=base_sha,
        diff_hash=diff_hash,
        summary=f"Static verification passed (V0 AST/compile delta and V1 isolated import check in Docker for {target_file}; testsRun=0).",
        valid_for_fast_lane=True,
    )
