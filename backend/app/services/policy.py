"""Deterministic final-diff, plan-scope, dependency, and mode-aware evidence checks."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import configparser
import io
from pathlib import PurePosixPath
import re
import tomllib
from typing import Any

HARD_CAPS: dict[str, int] = {
    "maxDiffFiles": 5,
    "maxDiffLines": 150,
    "maxRepairAttempts": 6,
    "maxModelCalls": 50,
    "taskTimeoutSeconds": 1800,
    "executionTimeoutSeconds": 300,
}

ALLOWED_CONFIG_PROFILES: frozenset[str] = frozenset({
    ".flake8",
    ".gitignore",
    ".editorconfig",
    ".python-version",
    "pytest.ini",
    "ruff.toml",
    ".ruff.toml",
    "mypy.ini",
    ".mypy.ini",
    ".coveragerc",
})

PROTECTED_BUILD_AND_CI_FILES: frozenset[str] = frozenset({
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Dockerfile",
    "dockerfile",
    "Jenkinsfile",
    "jenkinsfile",
    "Makefile",
    "makefile",
    "Pipfile",
    "pipfile",
    "Pipfile.lock",
    "poetry.lock",
    "uv.lock",
    "pdm.lock",
})

_ALLOWED_REQUIREMENTS_FILE_RE = re.compile(r"^requirements(?:[-_.][A-Za-z0-9_.-]+)?\.txt$")
_EXACT_REQ_PIN_RE = re.compile(
    r"^([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)==([A-Za-z0-9][A-Za-z0-9._+!-]*)$"
)
_UNSAFE_CONFIG_LINE_RE = re.compile(
    r"(?i)(?:`[^`]+`|\$\([^)]+\)|\b(?:subprocess|os\.system|os\.popen|eval\s*\(|exec\s*\(|pytest_plugins)\b)"
)


_KNOWN_TYPOSQUATS: frozenset[str] = frozenset({
    "reqeusts",
    "requeusts",
    "urllib4",
    "pyyaml-env",
    "python-dateutils",
    "beautifullsoup4",
    "bs4-html",
    "flaks",
    "fastpai",
    "pandass",
    "numppy",
    "scikitlearn",
    "cryptograpy",
})


def _norm_rel_path(path_str: str) -> str:
    return re.sub(r"^(?:\./)+", "", (path_str or "").replace("\\", "/").strip())


def canonicalize_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", (name or "").strip()).lower()


def is_allowed_requirements_file(path_str: str) -> bool:
    norm = _norm_rel_path(path_str)
    p = PurePosixPath(norm)
    if len(p.parts) != 1:
        return False
    return bool(_ALLOWED_REQUIREMENTS_FILE_RE.fullmatch(p.name))


def is_allowed_config_profile(path_str: str) -> bool:
    norm = _norm_rel_path(path_str)
    p = PurePosixPath(norm)
    if len(p.parts) != 1:
        return False
    return p.name in ALLOWED_CONFIG_PROFILES


def normalize_allowed_dependency_changes(
    raw_changes: Sequence[ Any ] | Mapping[str, str] | None,
) -> dict[str, tuple[str, str]]:
    """Validate and normalize approved dependency changes into `{pkg_lower: (exact_pkg, exact_version)}`.

    Raises ValueError if any dependency entry is missing an exact `package==version` pin
    or uses URLs, VCS refs, options, ranges, or local paths.
    """
    if not raw_changes:
        return {}
    approved: dict[str, tuple[str, str]] = {}
    if isinstance(raw_changes, Mapping):
        items: list[Any] = [
            {"package": k, "version": v, "reason": "approved"} for k, v in raw_changes.items()
        ]
    else:
        items = list(raw_changes)

    for entry in items:
        spec_str = ""
        if isinstance(entry, str):
            spec_str = entry.strip()
        elif isinstance(entry, Mapping):
            if "reason" in entry and not str(entry.get("reason") or "").strip():
                raise ValueError("Dependency change entry in approved plan requires a non-empty reason.")
            pkg = str(entry.get("package") or "").strip()
            ver = str(entry.get("version") or "").strip()
            spec_field = str(entry.get("spec") or entry.get("pin") or "").strip()
            if spec_field:
                spec_str = spec_field
            elif pkg and ver:
                spec_str = f"{pkg}=={ver}"
            elif pkg and "==" in pkg:
                spec_str = pkg
        if not spec_str:
            raise ValueError("Invalid dependency change entry: exact package==version is required.")
        if (
            spec_str.startswith("-")
            or "://" in spec_str
            or "git+" in spec_str
            or "@" in spec_str
            or ";" in spec_str
            or "/" in spec_str
            or "\\" in spec_str
            or ".." in spec_str
        ):
            raise ValueError(f"Unsupported or unsafe dependency specification: {spec_str}")
        m = _EXACT_REQ_PIN_RE.fullmatch(spec_str)
        if not m:
            raise ValueError(f"Dependency changes must use exact package==version pins: {spec_str}")
        pkg_name, version = m.group(1), m.group(2)
        if canonicalize_package_name(pkg_name) in _KNOWN_TYPOSQUATS:
            raise ValueError(f"Rejected suspected typosquat or unsafe package name: {pkg_name}")
        approved[pkg_name.lower()] = (pkg_name, version)
    return approved


def extract_allowed_dependency_changes(plan: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Extract normalized `package==version` strings from an approved plan dict."""
    if not isinstance(plan, Mapping):
        return ()
    raw = plan.get("dependency_changes") or plan.get("dependencyChanges") or ()
    if not raw:
        return ()
    approved = normalize_allowed_dependency_changes(raw)
    return tuple(f"{pkg}=={ver}" for _, (pkg, ver) in sorted(approved.items()))


def derive_limits(
    approved_plan: Mapping[str, Any] | None,
    hard_caps: Mapping[str, Any] | None = None,
) -> dict[str, int]:
    """Purely derive per-task execution/diff limits from the approved plan bounded by HARD_CAPS via min()."""
    derived: dict[str, int] = dict(HARD_CAPS)
    if isinstance(hard_caps, Mapping):
        for k, hard_val in HARD_CAPS.items():
            if k in hard_caps and isinstance(hard_caps[k], (int, float)) and int(hard_caps[k]) > 0:
                derived[k] = min(hard_val, int(hard_caps[k]))

    if not isinstance(approved_plan, Mapping):
        return derived

    expected_files = approved_plan.get("expected_files")
    if expected_files is None:
        expected_files = approved_plan.get("expectedFiles")
    if isinstance(expected_files, (list, tuple)) and len(expected_files) > 0:
        clean_files = [str(f).strip() for f in expected_files if str(f).strip()]
        if clean_files:
            derived["maxDiffFiles"] = min(derived["maxDiffFiles"], len(clean_files))

    explicit_max_files = approved_plan.get("max_diff_files") or approved_plan.get("maxDiffFiles")
    if isinstance(explicit_max_files, int) and explicit_max_files > 0:
        derived["maxDiffFiles"] = min(derived["maxDiffFiles"], explicit_max_files)

    line_budget = (
        approved_plan.get("line_budget")
        or approved_plan.get("lineBudget")
        or approved_plan.get("max_diff_lines")
        or approved_plan.get("maxDiffLines")
    )
    if isinstance(line_budget, int) and line_budget > 0:
        derived["maxDiffLines"] = min(derived["maxDiffLines"], line_budget)

    return derived


def is_protected_Code_or_build_path(
    path_str: str,
    *,
    allow_requirements: bool = False,
    allow_config_profile: bool = False,
) -> bool:
    norm = _norm_rel_path(path_str)
    p = PurePosixPath(norm)
    if (
        not norm
        or p.is_absolute()
        or ".." in p.parts
        or "\\" in path_str
        or ":" in norm
        or any(c.isspace() for c in norm)
    ):
        return True
    if any(part.casefold().startswith((".git", ".env", ".github", ".circleci")) for part in p.parts):
        return True
    if allow_config_profile and is_allowed_config_profile(norm):
        return False
    if allow_requirements and is_allowed_requirements_file(norm):
        return False
    if (
        p.name in PROTECTED_BUILD_AND_CI_FILES
        or p.name.casefold() in PROTECTED_BUILD_AND_CI_FILES
        or p.suffix.casefold() in {".lock", ".yaml", ".yml"}
        or p.name.casefold().startswith("requirements")
    ):
        return True
    return False


def validate_plan_scope(
    plan: Mapping[str, Any] | None,
    hard_caps: Mapping[str, Any] | None = None,
) -> None:
    """Reject a plan at plan-creation time if it exceeds hard caps, lists protected files, or has invalid dependency specs."""
    if not isinstance(plan, Mapping):
        return
    caps = dict(HARD_CAPS)
    if isinstance(hard_caps, Mapping):
        for k, v in HARD_CAPS.items():
            if k in hard_caps and isinstance(hard_caps[k], (int, float)) and int(hard_caps[k]) > 0:
                caps[k] = min(v, int(hard_caps[k]))

    task_type = str(plan.get("task_type") or plan.get("taskType") or "bug").strip().lower()
    raw_deps = plan.get("dependency_changes") or plan.get("dependencyChanges") or []
    if raw_deps:
        if not isinstance(raw_deps, Sequence) or isinstance(raw_deps, (str, bytes)):
            raise ValueError("Plan dependency_changes must be a list of approved package pins.")
        for item in raw_deps:
            if isinstance(item, Mapping):
                reason = str(item.get("reason") or "").strip()
                if not reason:
                    raise ValueError("Every plan dependency change must include a non-empty reason.")
                target_file = str(item.get("file") or item.get("path") or "requirements.txt").strip()
                if not is_allowed_requirements_file(target_file):
                    raise ValueError(f"Dependency changes are only supported in requirements*.txt, not {target_file}.")
        normalize_allowed_dependency_changes(raw_deps)

    expected_files = plan.get("expected_files")
    if expected_files is None:
        expected_files = plan.get("expectedFiles")
    if isinstance(expected_files, Sequence) and not isinstance(expected_files, (str, bytes)):
        if len(expected_files) > caps["maxDiffFiles"]:
            raise ValueError(
                f"Plan expected_files ({len(expected_files)}) exceeds hard cap of {caps['maxDiffFiles']} files."
            )

    line_budget = plan.get("line_budget") if plan.get("line_budget") is not None else plan.get("lineBudget")
    if isinstance(line_budget, int):
        if line_budget <= 0 or line_budget > caps["maxDiffLines"]:
            raise ValueError(
                f"Plan line_budget ({line_budget}) exceeds hard cap of {caps['maxDiffLines']} lines."
            )

    files_to_check: list[str] = []
    if isinstance(expected_files, Sequence) and not isinstance(expected_files, (str, bytes)):
        files_to_check.extend(str(f) for f in expected_files if f)
    likely_files = plan.get("likely_files") or plan.get("likelyFiles") or []
    if isinstance(likely_files, Sequence) and not isinstance(likely_files, (str, bytes)) and not files_to_check:
        files_to_check.extend(str(f) for f in likely_files if f)

    allow_reqs = bool(raw_deps)
    allow_cfg = task_type == "config"
    for fpath in files_to_check:
        if is_protected_Code_or_build_path(
            fpath,
            allow_requirements=allow_reqs,
            allow_config_profile=allow_cfg,
        ):
            raise ValueError(f"Plan targets protected or unsupported file path: {fpath}")
        if task_type == "config":
            norm_f = _norm_rel_path(fpath)
            p_name = PurePosixPath(norm_f).name
            is_test = p_name.startswith("test_") and p_name.endswith(".py")
            if not is_test and not is_allowed_config_profile(norm_f):
                raise ValueError(f"Config task plan targets file outside ALLOWED_CONFIG_PROFILES: {fpath}")


def _validate_requirements_diff_section(
    section: str,
    approved_deps: Mapping[str, tuple[str, str]],
) -> None:
    if not approved_deps:
        raise ValueError("Unapproved dependency file modification in requirements*.txt.")

    added_Lines: list[str] = []
    removed_lines: list[str] = []
    for line in section.splitlines():
        if line.startswith(("+++ ", "--- ", "@@ ", "diff --git ", "index ", "new file mode")):
            continue
        if line.startswith("+"):
            added_Lines.append(line[1:].strip())
        elif line.startswith("-"):
            removed_lines.append(line[1:].strip())

    added_pkgs: set[str] = set()
    for raw_line in added_Lines:
        if not raw_line or raw_line.startswith("#"):
            if raw_line.startswith("#") and any(opt in raw_line for opt in ("--index-url", "--extra-index-url", "--find-links")):
                raise ValueError("Unsafe pip option in requirements comment.")
            continue
        if (
            raw_line.startswith("-")
            or "://" in raw_line
            or "git+" in raw_line
            or "@" in raw_line
            or ";" in raw_line
            or "/" in raw_line
            or "\\" in raw_line
            or ".." in raw_line
        ):
            raise ValueError(f"Unsafe or unpinned dependency line in requirements diff: {raw_line}")
        m = _EXACT_REQ_PIN_RE.fullmatch(raw_line)
        if not m:
            raise ValueError(f"Dependency changes in requirements*.txt must use exact package==version pins: {raw_line}")
        pkg, ver = m.group(1), m.group(2)
        pkg_lower = pkg.lower()
        # Check both exact lowercase and canonical name to block typosquats and unlisted packages
        if pkg_lower not in approved_deps:
            canon = canonicalize_package_name(pkg)
            canon_matches = [
                (ap_pkg, ap_ver) for ap_key, (ap_pkg, ap_ver) in approved_deps.items()
                if canonicalize_package_name(ap_key) == canon
            ]
            if not canon_matches:
                raise ValueError(f"Unapproved dependency addition in requirements file: {pkg}=={ver}")
            ap_pkg, ap_ver = canon_matches[0]
            if pkg.lower() != ap_pkg.lower():
                raise ValueError(f"Dependency package name {pkg} does not match approved {ap_pkg}.")
            if ver != ap_ver:
                raise ValueError(f"Dependency version {pkg}=={ver} does not match approved {ap_pkg}=={ap_ver}.")
            added_pkgs.add(ap_pkg.lower())
        else:
            ap_pkg, ap_ver = approved_deps[pkg_lower]
            if ver != ap_ver:
                raise ValueError(f"Dependency version {pkg}=={ver} does not match approved {ap_pkg}=={ap_ver}.")
            added_pkgs.add(pkg_lower)

    for rem_line in removed_lines:
        if not rem_line or rem_line.startswith("#"):
            continue
        rem_pkg = re.split(r"[=<>!~;\s\[]", rem_line, maxsplit=1)[0].strip().lower()
        if rem_pkg and rem_pkg not in added_pkgs and rem_pkg not in approved_deps:
            raise ValueError(f"Unapproved dependency removal in requirements file: {rem_line}")


def validate_config_profile_diff_section(path_str: str, section: str) -> None:
    """Validate that a config profile diff modifies only an allowlisted profile with safe syntax."""
    if not is_allowed_config_profile(path_str):
        raise ValueError(f"Config path {path_str} is not in ALLOWED_CONFIG_PROFILES.")
    name = PurePosixPath(path_str.replace("\\", "/")).name
    added_lines: list[str] = []
    for line in section.splitlines():
        if line.startswith(("+++ ", "--- ", "@@ ", "diff --git ", "index ", "new file mode")):
            continue
        if line.startswith("+"):
            content_line = line[1:]
            if _UNSAFE_CONFIG_LINE_RE.search(content_line):
                raise ValueError(f"Unsafe directive in config profile {name}.")
            added_lines.append(content_line)

    added_block = "\n".join(added_lines).strip()
    if not added_block:
        return

    if name in {"ruff.toml", ".ruff.toml"}:
        try:
            tomllib.loads(added_block)
        except Exception as exc:
            raise ValueError(f"Invalid TOML in {name}: {exc}") from exc
    elif name == ".python-version":
        for ln in added_lines:
            s = ln.strip()
            if s and not s.startswith("#") and not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,2}(?:[a-z0-9.-]*)?", s):
                raise ValueError(f"Invalid Python version specifier in .python-version: {s}")
    elif name in {".flake8", "pytest.ini", "mypy.ini", ".mypy.ini", ".coveragerc", ".editorconfig"}:
        # Validate that added lines are either section headers, comments, key=value, or indented continuation lines
        for ln in added_lines:
            stripped = ln.strip()
            if not stripped or stripped.startswith(("#", ";")):
                continue
            if stripped.startswith("[") and stripped.endswith("]"):
                continue
            if "=" in stripped or ":" in stripped or ln.startswith((" ", "\t")):
                continue
            raise ValueError(f"Malformed INI line in {name}: {stripped}")


def validate_fix(
    diff: str,
    limits: dict,
    evidence: dict,
    base_sha: str | None,
    *,
    require_regression: bool = True,
    allowed_deletions=(),
    allow_static_oracle: bool = False,
    task_type: str | None = None,
    allowed_dependency_changes: Sequence[Any] | Mapping[str, str] | None = None,
) -> None:
    if not base_sha or not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise ValueError("A pinned base SHA is required.")

    effective_mode = str(task_type or evidence.get("taskType") or "bug").strip().lower()
    if effective_mode not in {"bug", "feature", "refactor", "config"}:
        raise ValueError(f"Unsupported or ambiguous task_type for validate_fix: {effective_mode}")

    # Fast-lane static oracle is strictly restricted to bug mode
    if effective_mode != "bug":
        allow_static_oracle = False
        if evidence.get("lane") == "fast":
            raise ValueError(f"Fast lane is disallowed for task_type={effective_mode}.")

    approved_deps = normalize_allowed_dependency_changes(
        allowed_dependency_changes
        if allowed_dependency_changes is not None
        else evidence.get("approvedDependencyChanges")
    )

    oracle_info = evidence.get("oracle") if isinstance(evidence.get("oracle"), dict) else {}
    static_ok = (
        effective_mode == "bug"
        and allow_static_oracle
        and evidence.get("lane") == "fast"
        and evidence.get("verificationLevel") in {"V0", "V1"}
        and evidence.get("reproduced") is True
        and evidence.get("regressionPassed") is True
        and type(evidence.get("testsRun")) is int
        and evidence["testsRun"] == 0
        and evidence.get("newFailures") == []
        and oracle_info.get("originalFailed") is True
        and oracle_info.get("patchedPassed") is True
        and oracle_info.get("validForFastLane") is True
    )

    if effective_mode == "bug":
        if not static_ok and not (
            evidence.get("reproduced") is True
            and evidence.get("regressionPassed") is True
            and type(evidence.get("testsRun")) is int
            and evidence["testsRun"] > 0
            and evidence.get("newFailures") == []
        ):
            raise ValueError("Passing reproduction, regression and test evidence is required.")
    elif effective_mode == "feature":
        if not (
            evidence.get("reproduced") is True
            and evidence.get("regressionPassed") is True
            and type(evidence.get("testsRun")) is int
            and evidence["testsRun"] > 0
            and evidence.get("newFailures") == []
        ):
            raise ValueError(
                "Feature verification requires acceptance tests that fail on original, pass on patched, and leave the existing suite green."
            )
    elif effective_mode == "refactor":
        if evidence.get("baselinePassed") is False:
            raise ValueError("Refactor verification requires behavior tests to pass before and after the change.")
        if evidence.get("touchedSymbolsCovered") is not True:
            raise ValueError("Refactor verification requires test coverage for all touched symbols.")
        if evidence.get("unapprovedExportChanges"):
            raise ValueError("Refactor changed public API exports without plan approval.")
        if not (
            evidence.get("reproduced") is True
            and evidence.get("regressionPassed") is True
            and type(evidence.get("testsRun")) is int
            and evidence["testsRun"] > 0
            and evidence.get("newFailures") == []
        ):
            raise ValueError(
                "Refactor verification requires behavior-preserving tests passing before and after with full suite green."
            )
    elif effective_mode == "config":
        if evidence.get("configProfileValidated") is not True:
            raise ValueError("Config verification requires deterministic config profile validation.")
        if not (
            evidence.get("regressionPassed") is True
            and type(evidence.get("testsRun")) is int
            and evidence["testsRun"] > 0
            and evidence.get("newFailures") == []
        ):
            raise ValueError("Config verification requires full test suite green with testsRun > 0.")

    if allowed_deletions:
        from sandbox.cleanup import safe_names
        safe_names(allowed_deletions)

    sections_by_path: dict[str, str] = {}
    for raw_section in re.split(r"(?=^diff --git )", diff or "", flags=re.MULTILINE):
        if not raw_section.strip():
            continue
        m_target = re.search(r"^\+\+\+ b/(.+)$", raw_section, flags=re.MULTILINE)
        if m_target:
            sections_by_path[m_target.group(1).strip()] = raw_section

    paths = []
    previous = None
    changes = 0
    git_paths = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            git_paths = line[len("diff --git "):].split(" ")
        if line.startswith(("old mode", "new mode", "rename ", "copy ", "GIT binary", "Binary files")):
            raise ValueError("Unsupported file operation.")
        if line.startswith("deleted file mode") and (line != "deleted file mode 100644" or not allowed_deletions):
            raise ValueError("Deletion requires an approved cleanup scope.")
        if line.startswith("new file mode") and line != "new file mode 100644":
            raise ValueError("Only regular files may be added.")
        if line.startswith("--- "):
            previous = line[4:]
        elif line.startswith("+++ "):
            target = line[4:]
            if target == "/dev/null":
                name = previous[2:] if previous and previous.startswith("a/") else ""
                if name not in allowed_deletions:
                    raise ValueError("Deletion is outside the approved cleanup scope.")
            else:
                if not target.startswith("b/") or previous not in ("/dev/null", "a/" + target[2:]):
                    raise ValueError("Deletion, rename or malformed diff header.")
                name = target[2:]
            if git_paths is not None and git_paths != ["a/" + name, "b/" + name]:
                raise ValueError("Conflicting diff paths.")
            path = PurePosixPath(name)
            if (
                not name
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in name
                or ":" in name
                or any(c.isspace() for c in name)
                or any(p.casefold().startswith((".git", ".env", ".github", ".circleci")) for p in path.parts)
            ):
                raise ValueError("Protected or unsafe diff path.")

            is_req_file = is_allowed_requirements_file(name)
            is_cfg_profile = is_allowed_config_profile(name)
            is_test_file = path.name.startswith("test_") and name.endswith(".py")

            if effective_mode == "config" and not is_test_file:
                if not is_cfg_profile:
                    raise ValueError("Protected or unsafe diff path: config mode only permits validated configuration profiles.")
                validate_config_profile_diff_section(name, sections_by_path.get(name, ""))
            elif is_req_file:
                if not approved_deps:
                    raise ValueError("Protected or unsafe diff path: unapproved dependency file modification in requirements*.txt.")
                if evidence.get("lane") == "fast":
                    raise ValueError("Dependency changes cannot use the fast lane.")
                if evidence.get("environmentRebuilt") is False:
                    raise ValueError("Approved dependency changes require a wheel-only environment rebuild.")
                _validate_requirements_diff_section(sections_by_path.get(name, ""), approved_deps)
            elif (
                path.name in PROTECTED_BUILD_AND_CI_FILES
                or path.name.casefold() in PROTECTED_BUILD_AND_CI_FILES
                or path.suffix.casefold() in {".lock", ".yaml", ".yml"}
                or path.name.casefold().startswith("requirements")
            ):
                raise ValueError("Protected or unsafe diff path.")

            paths.append(name)
        elif line.startswith(("+", "-")):
            changes += 1

    if not paths or len(set(paths)) != len(paths) or len(paths) > limits["maxDiffFiles"] or changes > limits["maxDiffLines"]:
        raise ValueError(
            f"Diff exceeds scope or has duplicate file sections: {len(paths)} files, {changes} changed lines; "
            f"limits {limits['maxDiffFiles']} files and {limits['maxDiffLines']} lines."
        )
    if require_regression and effective_mode in {"bug", "feature"}:
        if not any(PurePosixPath(p).name.startswith("test_") and p.endswith(".py") for p in paths):
            raise ValueError("A regression test must be included in the diff.")
