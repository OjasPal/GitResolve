"""Bounded static inspection. Never imports or executes repository modules."""
import ast
from dataclasses import dataclass, field, asdict
from pathlib import Path
import re
import tomllib

EXCLUDED = {".git", ".venv", "venv", "node_modules", "__pycache__", ".tox"}
LOCKS = {"uv.lock", "poetry.lock", "Pipfile.lock", "pdm.lock", "pylock.toml"}


def source_files(root):
    root = Path(root).resolve()
    found = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if any(p in EXCLUDED for p in rel.parts) or path.is_symlink():
            continue
        if any(p.is_symlink() for p in path.parents if p != root.parent):
            continue
        if path.is_file() and path.stat().st_size <= 100_000:
            found.append(path)
        if len(found) > 2000:
            raise ValueError("Repository exceeds static inspection file budget.")
    return sorted(found)


@dataclass
class RepositoryProfile:
    language: str = "unsupported"
    python_version: str = ""
    framework: str = "none"
    dependency_files: list[str] = field(default_factory=list)
    entrypoints: list[str] = field(default_factory=list)
    test_dirs: list[str] = field(default_factory=list)
    test_framework: str = "pytest"
    test_command: list[str] = field(default_factory=lambda: ["python", "-m", "pytest", "-q", "--tb=short"])
    env_names: list[str] = field(default_factory=list)
    syntax_errors: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def profile_repository(root):
    root = Path(root).resolve()
    result = RepositoryProfile()
    imports, env, tests = set(), set(), set()
    framework_entries = {}
    for path in source_files(root):
        name = path.relative_to(root).as_posix()
        if path.name == "pyproject.toml" or path.name.startswith("requirements") or path.name in LOCKS or path.name in {"Pipfile", "setup.py", "setup.cfg"}:
            result.dependency_files.append(name)
        if path.name == ".python-version":
            result.python_version = path.read_text().strip()[:40]
        if path.name == "pyproject.toml":
            try:
                project = tomllib.loads(path.read_text()).get("project", {})
                result.python_version = project.get("requires-python", result.python_version)
                if project:
                    result.language = "python"
            except (ValueError, UnicodeError):
                result.syntax_errors.append(name)
        if path.name == ".env.example":
            env.update(re.findall(r"(?m)^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=", path.read_text(errors="replace")))
        if path.suffix != ".py":
            continue
        result.language = "python"
        if path.name.startswith("test_") or path.name.endswith("_test.py"):
            tests.add(path.parent.relative_to(root).as_posix())
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeError):
            result.syntax_errors.append(name)
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for module in modules:
                    base = module.split(".")[0]
                    imports.add(base)
                    if base in {"streamlit", "fastapi", "flask", "django"}:
                        framework_entries.setdefault(base, []).append(name)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                target = ast.unparse(node.func)
                if target in {"os.getenv", "os.environ.get", "st.secrets.get", "streamlit.secrets.get"} and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    env.add(node.args[0].value)
            if isinstance(node, ast.Subscript) and ast.unparse(node.value) in {"os.environ", "st.secrets", "streamlit.secrets"}:
                if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                    env.add(node.slice.value)
        if path.name in {"app.py", "main.py", "manage.py", "__main__.py", "wsgi.py", "asgi.py"}:
            result.entrypoints.append(name)
    for framework in ("streamlit", "django", "fastapi", "flask"):
        if framework in imports:
            result.framework = framework
            result.entrypoints = sorted(set(framework_entries[framework] + result.entrypoints))
            break
    result.test_framework = "pytest" if "pytest" in imports or "unittest" not in imports else "unittest"
    result.test_dirs = sorted(tests)
    result.env_names = sorted(env)
    return result
