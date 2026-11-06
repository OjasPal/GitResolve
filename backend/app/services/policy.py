"""Deterministic final-diff and evidence checks, independent of model prompts."""
import re
from pathlib import PurePosixPath


def validate_fix(
    diff: str,
    limits: dict,
    evidence: dict,
    base_sha: str | None,
    *,
    require_regression: bool = True,
    allowed_deletions=(),
    allow_static_oracle: bool = False,
) -> None:
    if not base_sha or not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise ValueError("A pinned base SHA is required.")
    oracle_info = evidence.get("oracle") if isinstance(evidence.get("oracle"), dict) else {}
    static_ok = (
        allow_static_oracle
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
    if not static_ok and not (evidence.get("reproduced") is True and evidence.get("regressionPassed") is True
            and type(evidence.get("testsRun")) is int and evidence["testsRun"] > 0
            and evidence.get("newFailures") == []):
        raise ValueError("Passing reproduction, regression and test evidence is required.")
    if allowed_deletions:
        from sandbox.cleanup import safe_names
        safe_names(allowed_deletions)
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
            if (not name or path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name
                    or any(c.isspace() for c in name)
                    or any(p.casefold().startswith((".git", ".env")) for p in path.parts)
                    or path.name in {"pyproject.toml", "setup.py", "setup.cfg", "Dockerfile", "Jenkinsfile", "Makefile"}
                    or path.suffix in {".lock", ".yaml", ".yml"}
                    or path.name.startswith("requirements")):
                raise ValueError("Protected or unsafe diff path.")
            paths.append(name)
        elif line.startswith(("+", "-")):
            changes += 1
    if not paths or len(set(paths)) != len(paths) or len(paths) > limits["maxDiffFiles"] or changes > limits["maxDiffLines"]:
        raise ValueError(f"Diff exceeds scope or has duplicate file sections: {len(paths)} files, {changes} changed lines; limits {limits['maxDiffFiles']} files and {limits['maxDiffLines']} lines.")
    if require_regression and not any(PurePosixPath(p).name.startswith("test_") and p.endswith(".py") for p in paths):
        raise ValueError("A regression test must be included in the diff.")
