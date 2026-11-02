import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from sandbox.models import ExecutionRequest
from sandbox.container_entry import validate_patch


def test_expanded_scope_requires_exact_task_and_repo(monkeypatch):
    grant = {"tsk_approved": {"repo_url": "https://github.com/OjasPal/MCQGen", "max_diff_lines": 400}}
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: json.dumps(grant))
    args = {"execution_id": "tsk_approved", "repo_url": "https://github.com/OjasPal/MCQGen",
            "commit_sha": "a" * 40, "command": ["python", "-m", "pytest", "-q"], "max_diff_lines": 400}
    assert ExecutionRequest(**args).max_diff_lines == 400
    with pytest.raises(ValidationError):
        ExecutionRequest(**{**args, "execution_id": "tsk_other"})
    with pytest.raises(ValidationError):
        ExecutionRequest(**{**args, "repo_url": "https://github.com/other/repo"})


def test_default_scope_and_protected_paths_remain_enforced():
    patch = "--- /dev/null\n+++ b/test_new.py\n@@ -0,0 +1,200 @@\n" + "+pass\n" * 200
    with pytest.raises(ValueError, match="scope"):
        validate_patch(patch)
    assert validate_patch(patch, 400) == ["test_new.py"]
    with pytest.raises(ValueError, match="Protected"):
        validate_patch(patch.replace("test_new.py", ".env"), 400)
