import pytest

from app.agent.fake import _DIFF
from app.services.policy import validate_fix

LIMITS = {"maxDiffFiles": 5, "maxDiffLines": 150}
EVIDENCE = {"reproduced": True, "regressionPassed": True, "testsRun": 13, "newFailures": []}


def test_valid_fix_requires_test_evidence_and_pinned_base():
    validate_fix(_DIFF, LIMITS, EVIDENCE, "a" * 40)
    for evidence, base in [({}, "a" * 40), (EVIDENCE, None), ({**EVIDENCE, "newFailures": ["test_x"]}, "a" * 40)]:
        with pytest.raises(ValueError):
            validate_fix(_DIFF, LIMITS, evidence, base)


@pytest.mark.parametrize("path", ["../escape.py", ".github/workflows/x.yml", ".env", ".git/config",
                                 "pyproject.toml", "requirements.txt", "x.lock", "C:/escape.py"])
def test_protected_paths_are_rejected(path):
    with pytest.raises(ValueError):
        validate_fix(_DIFF.replace("src/slugify.py", path), LIMITS, EVIDENCE, "a" * 40)


def test_scope_and_regression_requirement():
    with pytest.raises(ValueError):
        validate_fix(_DIFF, {"maxDiffFiles": 1, "maxDiffLines": 150}, EVIDENCE, "a" * 40)
    with pytest.raises(ValueError):
        validate_fix(_DIFF, {"maxDiffFiles": 5, "maxDiffLines": 1}, EVIDENCE, "a" * 40)
    with pytest.raises(ValueError):
        validate_fix(_DIFF.split("--- /dev/null")[0], LIMITS, EVIDENCE, "a" * 40)
