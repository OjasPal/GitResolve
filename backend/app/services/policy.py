"""Deterministic final-diff and evidence checks, independent of model prompts."""
import re
from pathlib import PurePosixPath


def validate_fix(diff: str, limits: dict, evidence: dict, base_sha: str | None) -> None:
    if not base_sha or not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise ValueError("A pinned base SHA is required.")
    if not (evidence.get("reproduced") is True and evidence.get("regressionPassed") is True
            and type(evidence.get("testsRun")) is int and evidence["testsRun"] > 0
            and evidence.get("newFailures") == []):
        raise ValueError("Passing reproduction, regression and test evidence is required.")
    paths = []
    previous = None
    changes = 0
    for line in diff.splitlines():
        if line.startswith(("old mode", "new mode", "deleted file mode", "rename ", "copy ", "GIT binary", "Binary files")):
            raise ValueError("Unsupported file operation.")
        if line.startswith("new file mode") and line != "new file mode 100644":
            raise ValueError("Only regular files may be added.")
        if line.startswith("--- "):
            previous = line[4:]
        elif line.startswith("+++ "):
            target = line[4:]
            if not target.startswith("b/") or previous not in ("/dev/null", "a/" + target[2:]):
                raise ValueError("Deletion, rename or malformed diff header.")
            name = target[2:]
            path = PurePosixPath(name)
            if (not name or path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name
                    or any(c.isspace() for c in name)
                    or any(p.startswith((".git", ".env")) for p in path.parts)
                    or path.name in {"pyproject.toml", "setup.py", "setup.cfg", "Dockerfile", "Jenkinsfile", "Makefile"}
                    or path.suffix in {".lock", ".yaml", ".yml"}
                    or path.name.startswith("requirements")):
                raise ValueError("Protected or unsafe diff path.")
            paths.append(name)
        elif line.startswith(("+", "-")):
            changes += 1
    if not paths or len(set(paths)) != len(paths) or len(paths) > limits["maxDiffFiles"] or changes > limits["maxDiffLines"]:
        raise ValueError("Diff exceeds scope or has duplicate file sections.")
    if not any(PurePosixPath(p).name.startswith("test_") and p.endswith(".py") for p in paths):
        raise ValueError("A regression test must be included in the diff.")
