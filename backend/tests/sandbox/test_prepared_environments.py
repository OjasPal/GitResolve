"""Real Docker integration: never substitute mocked preparation for these tests."""
from pathlib import Path
import shutil
import subprocess
import pytest
from sandbox.models import ExecutionRequest
from sandbox.runner import run_sandbox
from sandbox.classification import classify

FIXTURES = Path(__file__).parents[1] / "fixtures" / "python_repos"


def ready():
    try:
        return all(subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=20).returncode == 0
                   for image in ("gitresolve-sandbox:poc", "gitresolve-sandbox:streamlit"))
    except (OSError, subprocess.TimeoutExpired):
        return False

pytestmark = pytest.mark.skipif(not ready(), reason="REAL DOCKER INTEGRATION SKIPPED: Docker and both reviewed sandbox images are required.")

@pytest.mark.parametrize("name,expected", [("stdlib", "passed"), ("requirements", "passed"), ("pyproject", "passed"),
    ("flask", "passed"), ("streamlit", "passed"), ("missing_dep", "missing_dep"),
    ("missing_secret", "missing_secret"), ("collection_error", "collection_error")])
def test_real_prepared_fixture(tmp_path, name, expected):
    def source(request, workspace, deadline, cancel):
        repo = Path(shutil.copytree(FIXTURES / name, workspace / "repo"))
        if name in {"requirements", "pyproject", "flask", "streamlit"}:
            (repo / "test_environment.py").write_text("import sys, pathlib, pytest, socket\ndef test_prepared_environment():\n    assert sys.prefix == '/opt/prepared'\n    with pytest.raises(OSError): pathlib.Path('/opt/prepared/forbidden').write_text('x')\n    with pytest.raises(OSError): socket.create_connection(('1.1.1.1', 443), timeout=0.3)\n")
        return repo
    request = ExecutionRequest(execution_id="prepared-" + name, repo_url="https://github.com/fixtures/python",
        commit_sha="a" * 40, command=["python", "-m", "pytest", "-q", "--tb=short"], timeout_seconds=300,
        prepare_dependencies=True, profile="streamlit" if name == "streamlit" else "python",
        ui_entrypoint="app.py" if name == "streamlit" else "")
    result = run_sandbox(request, source_provider=source)
    assert result.cleanup_ok
    assert classify(result) == expected, result.model_dump()
    if expected == "passed":
        assert result.exit_code == 0 and "passed" in result.stdout
