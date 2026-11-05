"""Trusted image entrypoint. Invoked with Python isolated mode, before repo code."""
import os
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys


def validate_patch(text, max_diff_lines=150, allowed_deletions=()):
    if allowed_deletions:
        try:
            from gitresolve_cleanup import safe_names
        except ImportError:
            from sandbox.cleanup import safe_names
        safe_names(allowed_deletions)
    if type(max_diff_lines) is not int or not 1 <= max_diff_lines <= 400:
        raise ValueError("Invalid patch line budget")
    if len(text.encode()) > 100_000:
        raise ValueError("Patch too large")
    names = []
    changes = 0
    old = None
    git_paths = None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            git_paths = line[len("diff --git "):].split(" ")
        if line.startswith(("old mode", "new mode", "rename ", "copy ", "GIT binary", "Binary files")):
            raise ValueError("Unsupported patch operation")
        if line.startswith("deleted file mode") and (line != "deleted file mode 100644" or not allowed_deletions):
            raise ValueError("Deletion requires an approved root-test scope")
        if line.startswith("new file mode") and line != "new file mode 100644":
            raise ValueError("Only regular files are allowed")
        if line.startswith("--- "):
            old = line[4:]
        elif line.startswith("+++ "):
            name = line[4:]
            if name == "/dev/null":
                name = old[2:] if old and old.startswith("a/") else ""
                if name not in allowed_deletions:
                    raise ValueError("Deletion is outside approved cleanup scope")
            else:
                if not name.startswith("b/") or old not in ("/dev/null", "a/" + name[2:]):
                    raise ValueError("Invalid patch path")
                name = name[2:]
            if git_paths is not None and git_paths != ["a/" + name, "b/" + name]:
                raise ValueError("Conflicting patch paths")
            path = PurePosixPath(name)
            if (not name or path.is_absolute() or ".." in path.parts or ":" in name or "\\" in name
                    or any(c.isspace() for c in name)
                    or any(p.casefold().startswith((".git", ".env")) for p in path.parts)
                    or path.name in {"pyproject.toml", "setup.py", "setup.cfg", "Dockerfile", "Makefile", "Jenkinsfile"}
                    or path.suffix in {".lock", ".yml", ".yaml"} or path.name.startswith("requirements")):
                raise ValueError("Protected patch path")
            names.append(name)
        elif line.startswith(("+", "-")):
            changes += 1
    if not names or len(set(names)) != len(names) or len(names) > 5 or changes > max_diff_lines:
        raise ValueError("Patch scope exceeds limits")
    if allowed_deletions and (set(names)!=set(allowed_deletions) or any(line != "+++ /dev/null" for line in text.splitlines() if line.startswith("+++ "))):
        raise ValueError("Cleanup scope permits only its exact complete-file deletions")
    return names


def main():
    shutil.copytree("/input", "/workspace", dirs_exist_ok=True)
    patch = Path("/patch.diff").read_text() if Path("/patch.diff").exists() else ""
    if patch:
        allowed = json.loads(os.environ.get("GITRESOLVE_DELETE_PATHS", "[]"))
        names = validate_patch(patch, int(os.environ.get("GITRESOLVE_MAX_DIFF_LINES", "150")), allowed)
        for name in names:
            path = Path("/workspace") / name
            if path.exists() and (not path.is_file() or path.stat().st_size > 100_000):
                raise ValueError("Patch target is not a small regular file")
        env = {"PATH": os.environ["PATH"], "HOME": "/tmp", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
        for options in (["--check"], []):
            applied = subprocess.run(["git", "apply", *options, "/patch.diff"], cwd="/workspace", env=env,
                                     timeout=10, capture_output=True, text=True)
            if applied.returncode:
                # The host sanitizes this bounded output before storing/displaying it.
                print(applied.stderr[:2000], file=sys.stderr)
                raise ValueError("Git rejected the proposed patch.")
        for name in names:
            path = Path("/workspace") / name
            if name in allowed and not path.exists():
                continue
            if path.is_symlink() or path.stat().st_size > 100_000:
                raise ValueError("Invalid patched file")
    os.chdir("/workspace")
    if os.environ.get("GITRESOLVE_VERIFY_CLEANUP") == "1":
        os.execvp("python", ["python", "-I", "/usr/local/lib/gitresolve_cleanup_verify.py"])
    if os.environ.get("GITRESOLVE_STREAMLIT_ENTRY"):
        if sys.argv[1:4] != ["python", "-m", "pytest"]:
            raise ValueError("Streamlit verification requires pytest.")
        os.execvp("python", ["python", "-I", "/usr/local/lib/gitresolve_streamlit_pytest.py", *sys.argv[4:]])
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Sandbox preparation or patch application failed.", file=sys.stderr)
        sys.exit(125)
