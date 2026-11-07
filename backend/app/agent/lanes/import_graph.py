"""Conservative AST import-graph selective test selector (V2 inner-loop accelerator)."""
from __future__ import annotations

import ast
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class V2SelectionResult:
    selected_tests: tuple[str, ...]
    total_tests: int
    saved_tests: int
    used_v2: bool
    fallback_reason: str = ""

    @property
    def used_fallback(self) -> bool:
        return not self.used_v2

    @property
    def total_test_files(self) -> int:
        return self.total_tests

    def to_dict(self) -> dict[str, object]:
        return {
            "selectedTests": list(self.selected_tests),
            "totalTests": self.total_tests,
            "totalTestFiles": self.total_tests,
            "savedTests": self.saved_tests,
            "usedV2": self.used_v2,
            "usedFallback": not self.used_v2,
            "fallbackReason": self.fallback_reason,
        }


def _is_test_file(rel_path: str) -> bool:
    norm = rel_path.replace("\\", "/").lstrip("./")
    p = PurePosixPath(norm)
    name = p.name.casefold()
    return (
        (name.startswith("test_") and name.endswith(".py"))
        or name.endswith("_test.py")
    )


def _module_candidates_for_path(rel_path: str) -> tuple[str, ...]:
    norm = rel_path.replace("\\", "/").lstrip("./")
    if not norm.endswith(".py"):
        return ()
    p = PurePosixPath(norm)
    parts = list(p.parts)
    if not parts:
        return ()
    if parts[-1] == "__init__.py":
        mod_parts = parts[:-1]
    else:
        mod_parts = parts[:-1] + [p.stem]
    if not mod_parts:
        return ()
    full_dotted = ".".join(mod_parts)
    candidates = [full_dotted]
    if mod_parts[0] in {"src", "lib", "app"} and len(mod_parts) > 1:
        candidates.append(".".join(mod_parts[1:]))
    if len(mod_parts) > 1:
        candidates.append(mod_parts[-1])
    return tuple(dict.fromkeys(candidates))


def _has_dynamic_import_or_plugin(tree: ast.Module, is_conftest: bool) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root == "importlib":
                    return "dynamic_importlib"
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root == "importlib":
                return "dynamic_importlib"
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in {"__import__", "eval", "exec"}:
                return f"dynamic_call:{node.func.id}"
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "importlib"
            ):
                return "dynamic_importlib_call"
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "pytest_plugins":
                    return "pytest_plugins_defined"
        if is_conftest and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("pytest_"):
                return f"conftest_hook:{node.name}"
            for dec in node.decorator_list:
                try:
                    dec_str = ast.unparse(dec)
                except Exception:
                    dec_str = ""
                if "fixture" in dec_str and "autouse=True" in dec_str.replace(" ", ""):
                    return "conftest_autouse_fixture"
    return None


def _resolve_relative_import(
    importer_path: str,
    level: int,
    module: str | None,
    module_index: Mapping[str, set[str]],
) -> set[str] | None:
    norm = importer_path.replace("\\", "/").lstrip("./")
    p = PurePosixPath(norm)
    pkg_parts = list(p.parts[:-1])
    up = max(0, level - 1)
    if up > len(pkg_parts):
        return None
    base_parts = pkg_parts[: len(pkg_parts) - up]
    target_parts = base_parts + (module.split(".") if module else [])
    if not target_parts:
        return None
    dotted = ".".join(target_parts)
    matched = set(module_index.get(dotted, ()))
    if not matched and len(target_parts) > 1 and target_parts[0] in {"src", "lib", "app"}:
        matched = set(module_index.get(".".join(target_parts[1:]), ()))
    return matched if matched else None


def select_tests_v2(
    repo_files: Mapping[str, str],
    changed_files: Sequence[str],
) -> V2SelectionResult:
    """Conservatively map changed Python modules to transitive dependent test files.

    Falls back to the full test suite (`used_v2=False`, `selected_tests=()`) on any
    dynamic import, conftest/plugin ambiguity, non-Python file edit, syntax error,
    or unresolved relative import.
    """
    norm_repo: dict[str, str] = {
        k.replace("\\", "/").lstrip("./"): v for k, v in repo_files.items()
    }
    all_tests = sorted(p for p in norm_repo if _is_test_file(p))
    total_tests = len(all_tests)

    if total_tests == 0:
        return V2SelectionResult((), 0, 0, False, "no_test_files")

    norm_changed = [
        c.replace("\\", "/").lstrip("./") for c in changed_files if c and c.strip()
    ]
    if not norm_changed:
        return V2SelectionResult((), total_tests, 0, False, "no_changed_files")

    for ch in norm_changed:
        name_lower = PurePosixPath(ch).name.casefold()
        if name_lower == "conftest.py":
            return V2SelectionResult((), total_tests, 0, False, "conftest_modified")
        if not ch.endswith(".py"):
            return V2SelectionResult((), total_tests, 0, False, f"non_python_edit:{ch}")
        if ch not in norm_repo:
            return V2SelectionResult((), total_tests, 0, False, f"unknown_changed_module:{ch}")

    # Build module name -> file paths index
    module_index: dict[str, set[str]] = {}
    for rel_path in norm_repo:
        if not rel_path.endswith(".py"):
            continue
        for cand in _module_candidates_for_path(rel_path):
            module_index.setdefault(cand, set()).add(rel_path)

    # Reverse dependency graph: dependency_path -> set of importer_paths
    reverse_deps: dict[str, set[str]] = {p: set() for p in norm_repo if p.endswith(".py")}

    for rel_path, source in sorted(norm_repo.items()):
        if not rel_path.endswith(".py"):
            continue
        is_conftest = PurePosixPath(rel_path).name.casefold() == "conftest.py"
        try:
            tree = ast.parse(source, filename=rel_path)
        except SyntaxError:
            return V2SelectionResult((), total_tests, 0, False, f"syntax_error:{rel_path}")

        dyn_reason = _has_dynamic_import_or_plugin(tree, is_conftest=is_conftest)
        if dyn_reason is not None:
            return V2SelectionResult((), total_tests, 0, False, f"{dyn_reason}:{rel_path}")

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_name = alias.name
                    matched = set(module_index.get(imported_name, ()))
                    if not matched and "." in imported_name:
                        matched = set(module_index.get(imported_name.split(".")[0], ()))
                    for dep_path in matched:
                        if dep_path != rel_path:
                            reverse_deps.setdefault(dep_path, set()).add(rel_path)
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    resolved = _resolve_relative_import(rel_path, node.level, node.module, module_index)
                    if resolved is None:
                        # Also check if alias names resolve as submodules of the relative package
                        alias_resolved: set[str] = set()
                        for alias in node.names:
                            if alias.name == "*":
                                return V2SelectionResult((), total_tests, 0, False, f"star_relative_import:{rel_path}")
                            sub_mod = f"{node.module}.{alias.name}" if node.module else alias.name
                            sub_match = _resolve_relative_import(rel_path, node.level, sub_mod, module_index)
                            if sub_match:
                                alias_resolved.update(sub_match)
                        if not alias_resolved:
                            return V2SelectionResult((), total_tests, 0, False, f"unresolved_relative_import:{rel_path}")
                        resolved = alias_resolved
                    for dep_path in resolved:
                        if dep_path != rel_path:
                            reverse_deps.setdefault(dep_path, set()).add(rel_path)
                else:
                    mod_name = node.module or ""
                    matched = set(module_index.get(mod_name, ()))
                    for alias in node.names:
                        if alias.name == "*":
                            # Star import from a local module is still tracked if the module resolves
                            continue
                        sub_cand = f"{mod_name}.{alias.name}" if mod_name else alias.name
                        matched.update(module_index.get(sub_cand, ()))
                    if not matched and "." in mod_name:
                        matched.update(module_index.get(mod_name.split(".")[0], ()))
                    for dep_path in matched:
                        if dep_path != rel_path:
                            reverse_deps.setdefault(dep_path, set()).add(rel_path)

    # BFS from changed_files across reverse_deps
    visited: set[str] = set(norm_changed)
    queue: deque[str] = deque(norm_changed)
    while queue:
        curr = queue.popleft()
        for dependent in reverse_deps.get(curr, ()):
            if dependent not in visited:
                visited.add(dependent)
                queue.append(dependent)

    selected = tuple(sorted(p for p in visited if _is_test_file(p)))
    if not selected:
        return V2SelectionResult((), total_tests, 0, False, "no_reachable_tests")

    saved = max(0, total_tests - len(selected))
    return V2SelectionResult(
        selected_tests=selected,
        total_tests=total_tests,
        saved_tests=saved,
        used_v2=True,
        fallback_reason="",
    )
