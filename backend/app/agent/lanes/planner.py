"""Deterministic post-patch verification planner computed from the ACTUAL diff."""
from __future__ import annotations

import ast
from pathlib import PurePosixPath
import re
from typing import Any

from app.agent.lanes.triage import (
    PURE_STDLIB_MODULES,
    SIDE_EFFECT_STDLIB_MODULES,
    STDLIB_MODULES,
    analyze_python_source,
    is_override_path,
)
from app.agent.lanes.types import (
    ChangeClass,
    LaneName,
    PatchFacts,
    RiskFloor,
    TriageReport,
    VerificationLevel,
    VerificationPlan,
)


def apply_unified_diff(originals: dict[str, str], diff_text: str) -> dict[str, str]:
    """Reconstruct patched file contents from a unified diff against originals."""
    patched = dict(originals)
    sections = re.split(r"(?=^diff --git )", diff_text or "", flags=re.MULTILINE)
    for section in sections:
        if not section.strip():
            continue
        lines = section.splitlines()
        old_path = None
        new_path = None
        hunk_start_idx = None
        for idx, line in enumerate(lines):
            if line.startswith("--- "):
                old_path = line[4:].strip()
            elif line.startswith("+++ "):
                new_path = line[4:].strip()
            elif line.startswith("@@ "):
                hunk_start_idx = idx
                break
        if new_path == "/dev/null" and old_path and old_path.startswith("a/"):
            patched.pop(old_path[2:], None)
            continue
        if not new_path or not new_path.startswith("b/"):
            continue
        target = new_path[2:]
        if hunk_start_idx is None:
            continue
        orig_lines = originals.get(target, "").splitlines()
        out_lines: list[str] = []
        orig_pos = 0
        idx = hunk_start_idx
        while idx < len(lines):
            header = lines[idx]
            m = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", header)
            if not m:
                idx += 1
                continue
            start_old = int(m.group(1))
            count_old = int(m.group(2)) if m.group(2) is not None else 1
            target_orig_idx = max(0, start_old - 1) if count_old > 0 else orig_pos
            while orig_pos < target_orig_idx and orig_pos < len(orig_lines):
                out_lines.append(orig_lines[orig_pos])
                orig_pos += 1
            idx += 1
            while idx < len(lines) and not lines[idx].startswith("@@ "):
                hline = lines[idx]
                if hline.startswith("\\ No newline at end of file"):
                    idx += 1
                    continue
                if hline.startswith("+"):
                    out_lines.append(hline[1:])
                elif hline.startswith("-"):
                    orig_pos += 1
                elif hline.startswith(" "):
                    out_lines.append(hline[1:])
                    orig_pos += 1
                idx += 1
        while orig_pos < len(orig_lines):
            out_lines.append(orig_lines[orig_pos])
            orig_pos += 1
        patched[target] = "\n".join(out_lines) + ("\n" if out_lines else "")
    return patched


def _count_diff_lines(before: str, after: str) -> tuple[int, int]:
    import difflib
    added = 0
    removed = 0
    for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm=""):
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def _strip_docstrings_and_constants(tree: ast.AST) -> str:
    class StripDocstrings(ast.NodeTransformer):
        def _strip_body(self, body: list[ast.stmt]) -> list[ast.stmt]:
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                return body[1:] or [ast.Pass()]
            return body

        def visit_Module(self, node: ast.Module) -> ast.AST:
            self.generic_visit(node)
            node.body = self._strip_body(node.body)
            return node

        def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
            self.generic_visit(node)
            node.body = self._strip_body(node.body)
            return node

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
            self.generic_visit(node)
            node.body = self._strip_body(node.body)
            return node

        def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
            self.generic_visit(node)
            node.body = self._strip_body(node.body)
            return node

    import copy
    cloned = copy.deepcopy(tree)
    transformed = StripDocstrings().visit(cloned)
    return ast.dump(transformed, include_attributes=False)


def _symbol_referenced_across_repo(
    originals: dict[str, str],
    touched_path: str,
    symbol_name: str,
) -> bool:
    mod_stem = PurePosixPath(touched_path.replace("\\", "/")).stem
    for other_path, content in originals.items():
        if other_path == touched_path or not other_path.endswith(".py"):
            continue
        if symbol_name not in content and mod_stem not in content:
            continue
        try:
            tree = ast.parse(content, filename=other_path)
        except SyntaxError:
            if symbol_name in content:
                return True
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = (node.module or "").split(".")[-1]
                if mod == mod_stem:
                    for alias in node.names:
                        if alias.name in {symbol_name, "*"}:
                            return True
            elif isinstance(node, ast.Attribute) and node.attr == symbol_name:
                return True
    return False


def plan_verification(
    originals: dict[str, str],
    patched_or_diff: dict[str, str] | str,
    triage: TriageReport,
    declared_deps: set[str] | None = None,
) -> VerificationPlan:
    """Classify the actual diff and select the final lane and verification level."""
    patched_map = (
        apply_unified_diff(originals, patched_or_diff)
        if isinstance(patched_or_diff, str)
        else dict(patched_or_diff)
    )
    declared = {d.casefold().replace("-", "_") for d in (declared_deps or set())}

    all_keys = sorted(set(originals) | set(patched_map))
    touched: list[str] = []
    added_lines = 0
    removed_lines = 0
    override_reasons: list[str] = list(triage.override_reasons)

    for key in all_keys:
        before = originals.get(key, "")
        after = patched_map.get(key, "")
        if before != after:
            touched.append(key)
            add_c, rem_c = _count_diff_lines(before, after)
            added_lines += add_c
            removed_lines += rem_c
            if key not in originals or key not in patched_map:
                override_reasons.append("file_creation_or_deletion")
            override_reasons.extend(is_override_path(key))

    if not touched:
        override_reasons.append("empty_patch")
    elif len(touched) > 1:
        override_reasons.append("multi_file_patch")

    if triage.uncertain_localization:
        override_reasons.append("uncertain_localization")
    if triage.localized_files and any(f not in triage.localized_files for f in touched):
        override_reasons.append("scope_growth_outside_localized_files")
    if triage.risk_floor != RiskFloor.LOW:
        override_reasons.append(f"triage_risk_floor_{triage.risk_floor.value}")

    added_imports: list[str] = []
    removed_imports: list[str] = []
    new_third_party: list[str] = []
    resolved_undef: list[str] = []
    referenced_removals: list[str] = []
    side_effect_removals: list[str] = []
    changed_exports: list[str] = []
    all_non_import_unchanged = bool(touched)
    only_docstring_or_comment = bool(touched)

    for path in touched:
        if not path.endswith(".py") or path not in originals or path not in patched_map:
            all_non_import_unchanged = False
            only_docstring_or_comment = False
            override_reasons.append("non_python_or_missing_original")
            continue

        orig_src = originals[path]
        patch_src = patched_map[path]
        orig_analysis = analyze_python_source(orig_src, path)
        patch_analysis = analyze_python_source(patch_src, path)

        if not orig_analysis.syntax_valid or not patch_analysis.syntax_valid:
            all_non_import_unchanged = False
            only_docstring_or_comment = False
            override_reasons.append("syntax_error_in_source")
            continue

        if orig_analysis.has_dynamic_scope or patch_analysis.has_dynamic_scope:
            override_reasons.append("dynamic_scope_or_star_import")

        if orig_analysis.risky_constructs or patch_analysis.risky_constructs:
            override_reasons.extend(orig_analysis.risky_constructs)
            override_reasons.extend(patch_analysis.risky_constructs)

        if patch_analysis.undefined_names:
            override_reasons.append("unresolved_undefined_names_remain")

        if orig_analysis.exported_names != patch_analysis.exported_names:
            diff_exports = set(orig_analysis.exported_names) ^ set(patch_analysis.exported_names)
            changed_exports.extend(sorted(diff_exports))
            override_reasons.append("public_api_or_init_export")

        orig_imports = orig_analysis.imported_bindings
        patch_imports = patch_analysis.imported_bindings

        for bound_name, mod_root in patch_imports.items():
            if bound_name not in orig_imports or orig_imports[bound_name] != mod_root:
                added_imports.append(f"{mod_root}:{bound_name}")
                norm_mod = mod_root.casefold().replace("-", "_")
                if mod_root not in STDLIB_MODULES and norm_mod not in declared:
                    new_third_party.append(mod_root)
                    override_reasons.append("new_third_party_dependency")

        for bound_name, mod_root in orig_imports.items():
            if bound_name not in patch_imports:
                removed_imports.append(f"{mod_root}:{bound_name}")
                # Check if bound_name was actually unused in original AND not referenced in patched or across repo
                if (
                    bound_name not in orig_analysis.unused_imports
                    or _symbol_referenced_across_repo(originals, path, bound_name)
                ):
                    referenced_removals.append(bound_name)
                    override_reasons.append("referenced_removal")
                # Side-effect analysis on removed import
                if mod_root not in PURE_STDLIB_MODULES or mod_root in SIDE_EFFECT_STDLIB_MODULES:
                    side_effect_removals.append(f"{mod_root}:{bound_name}")
                    override_reasons.append("possible_side_effect_removal")

        for undef in orig_analysis.undefined_names:
            if undef not in patch_analysis.undefined_names and undef in patch_imports:
                resolved_undef.append(undef)

        if orig_analysis.non_import_ast_dump != patch_analysis.non_import_ast_dump:
            all_non_import_unchanged = False
            try:
                orig_tree = ast.parse(orig_src, filename=path)
                patch_tree = ast.parse(patch_src, filename=path)
                if _strip_docstrings_and_constants(orig_tree) != _strip_docstrings_and_constants(patch_tree):
                    only_docstring_or_comment = False
            except SyntaxError:
                only_docstring_or_comment = False

    dedup_overrides = tuple(dict.fromkeys(override_reasons))

    # Determine ChangeClass
    if len(touched) > 1:
        change_class = ChangeClass.MULTI_FILE_CHANGE
    elif dedup_overrides and any(
        r in dedup_overrides
        for r in (
            "auth_security_crypto",
            "serialization",
            "subprocess_eval",
            "sql_execution",
            "secrets_like_content",
            "ci_docker_dependency_or_config_path",
            "migration_path",
            "public_api_or_init_export",
            "test_or_conftest_edit",
            "risky_domain_path",
            "referenced_removal",
            "possible_side_effect_removal",
            "new_third_party_dependency",
        )
    ):
        change_class = ChangeClass.RISKY_OVERRIDE
    elif all_non_import_unchanged:
        if resolved_undef and added_imports and removed_imports and not referenced_removals and not side_effect_removals:
            change_class = ChangeClass.MISSING_IMPORT_AND_UNUSED_REMOVAL
        elif resolved_undef and added_imports and not removed_imports:
            change_class = ChangeClass.MISSING_IMPORT
        elif removed_imports and not added_imports and not referenced_removals and not side_effect_removals:
            change_class = ChangeClass.UNUSED_IMPORT_REMOVAL
        else:
            change_class = ChangeClass.UNKNOWN
    elif only_docstring_or_comment:
        change_class = ChangeClass.COMMENT_DOCSTRING_TYPO
    else:
        change_class = ChangeClass.BEHAVIOR_CHANGE

    facts = PatchFacts(
        touched_files=tuple(touched),
        added_lines=added_lines,
        removed_lines=removed_lines,
        change_class=change_class,
        added_imports=tuple(added_imports),
        removed_imports=tuple(removed_imports),
        new_third_party_imports=tuple(new_third_party),
        resolved_undefined_names=tuple(resolved_undef),
        referenced_removals=tuple(referenced_removals),
        side_effect_removals=tuple(side_effect_removals),
        changed_exports=tuple(changed_exports),
        override_reasons=dedup_overrides,
        non_import_ast_unchanged=all_non_import_unchanged,
    )

    allowlisted_class = change_class in {
        ChangeClass.MISSING_IMPORT,
        ChangeClass.MISSING_IMPORT_AND_UNUSED_REMOVAL,
        ChangeClass.UNUSED_IMPORT_REMOVAL,
    }
    has_static_witness = bool(resolved_undef) or (
        change_class == ChangeClass.UNUSED_IMPORT_REMOVAL and bool(removed_imports)
    )

    escalation_reasons: list[str] = list(dedup_overrides)
    if not allowlisted_class:
        escalation_reasons.append(f"disallowed_change_class:{change_class.value}")
    if not has_static_witness:
        escalation_reasons.append("no_failing_original_oracle_witness")

    is_fast_eligible = (
        not dedup_overrides
        and allowlisted_class
        and has_static_witness
        and all_non_import_unchanged
        and len(touched) == 1
        and added_lines + removed_lines <= 10
    )

    if is_fast_eligible:
        return VerificationPlan(
            final_lane=LaneName.FAST,
            verification_level=VerificationLevel.V1,
            patch_facts=facts,
            requires_v0=True,
            requires_v1=True,
            requires_full_suite=False,
            escalation_reasons=(),
        )

    return VerificationPlan(
        final_lane=LaneName.FULL,
        verification_level=VerificationLevel.V3,
        patch_facts=facts,
        requires_v0=False,
        requires_v1=False,
        requires_full_suite=True,
        escalation_reasons=tuple(dict.fromkeys(escalation_reasons)),
    )
