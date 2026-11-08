from pathlib import Path
from types import SimpleNamespace
import pytest
from sandbox.profiler import profile_repository
from sandbox.classification import classify

FIXTURES = Path(__file__).parents[1] / "fixtures" / "python_repos"

@pytest.mark.parametrize("name,framework,deps", [("stdlib", "none", []),
    ("requirements", "none", ["requirements.txt"]), ("pyproject", "none", ["pyproject.toml"]),
    ("flask", "flask", ["requirements.txt"]), ("streamlit", "streamlit", ["requirements.txt"])])
def test_five_profiles(name, framework, deps):
    profile = profile_repository(FIXTURES / name)
    assert profile.language == "python"
    assert profile.framework == framework
    assert profile.dependency_files == deps
    assert profile.test_dirs == ["."]


def test_environment_names_and_syntax():
    assert profile_repository(FIXTURES / "missing_secret").env_names == ["SERVICE_API_KEY"]
    assert profile_repository(FIXTURES / "collection_error").syntax_errors == ["test_app.py"]


def test_non_python_is_explicit(tmp_path):
    (tmp_path / "main.js").write_text("console.log('hello')")
    assert profile_repository(tmp_path).language == "unsupported"

@pytest.mark.parametrize("code,text,expected", [(1, "ModuleNotFoundError: No module named x", "missing_dep"),
    (2, "ERROR collecting", "collection_error"), (1, "1 failed", "preexisting_test_failure"),
    (1, "Missing required API key", "missing_secret"), (125, "preparation failed", "sandbox_prep_failure"),
    (5, "no tests ran", "no_tests"), (0, "1 passed", "passed")])
def test_classification(code, text, expected):
    result = SimpleNamespace(exit_code=code, stdout=text, stderr="", status="failed", cleanup_ok=True, oom_killed=False)
    assert classify(result) == expected


def test_ranked_context_is_bounded_and_line_numbered(tmp_path):
    from app.agent.repair_runner import repository_context
    for i in range(15):
        (tmp_path / f"aaa{i}.py").write_text("irrelevant = 1\n" * 100)
    (tmp_path / "z_parser.py").write_text("def parse_duration():\n    return 0\n")
    result = repository_context(tmp_path, 1800, "z_parser.py parse_duration traceback")
    assert len(result) <= 1800
    assert "1: def parse_duration" in result
    assert result.index("--- z_parser.py") < result.index("--- aaa0.py")
