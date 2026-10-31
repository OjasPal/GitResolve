"""Trusted image entrypoint. Invoked with Python isolated mode, before repo code."""
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys


def validate_patch(text):
    if len(text.encode()) > 100_000:
        raise ValueError("Patch too large")
    names = []
    changes = 0
    old = None
    git_paths = None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            git_paths = line[len("diff --git "):].split(" ")
        if line.startswith(("old mode", "new mode", "deleted file", "rename ", "copy ", "GIT binary", "Binary files")):
            raise ValueError("Unsupported patch operation")
        if line.startswith("new file mode") and line != "new file mode 100644":
            raise ValueError("Only regular files are allowed")
        if line.startswith("--- "):
            old = line[4:]
        elif line.startswith("+++ "):
            name = line[4:]
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
    if not names or len(set(names)) != len(names) or len(names) > 5 or changes > 150:
        raise ValueError("Patch scope exceeds limits")
    return names


def main():
    shutil.copytree("/input", "/workspace", dirs_exist_ok=True)
    patch = Path("/patch.diff").read_text() if Path("/patch.diff").exists() else ""
    if patch:
        names = validate_patch(patch)
        for name in names:
            path = Path("/workspace") / name
            if path.exists() and (not path.is_file() or path.stat().st_size > 100_000):
                raise ValueError("Patch target is not a small regular file")
        env = {"PATH": os.environ["PATH"], "HOME": "/tmp", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
        for options in (["--check"], []):
            subprocess.run(["git", "apply", *options, "/patch.diff"], cwd="/workspace", env=env,
                           check=True, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for name in names:
            path = Path("/workspace") / name
            if path.is_symlink() or path.stat().st_size > 100_000:
                raise ValueError("Invalid patched file")
    os.chdir("/workspace")
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Sandbox preparation or patch application failed.", file=sys.stderr)
        sys.exit(125)
