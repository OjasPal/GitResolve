import pytest
from app.services.publication_patch import publication_patch, validate_publication

IMPLEMENTATION = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+fixed\n"
TEST = "diff --git a/test_generated.py b/test_generated.py\nnew file mode 100644\n--- /dev/null\n+++ b/test_generated.py\n@@ -0,0 +1 @@\n+assert True\n"
LIMITS = {"maxDiffFiles": 5, "maxDiffLines": 150}
EVIDENCE = {"reproduced": True, "regressionPassed": True, "testsRun": 1, "newFailures": []}

def test_new_tests_are_excluded_but_evidence_is_required():
    tested = IMPLEMENTATION + TEST
    assert publication_patch(tested) == IMPLEMENTATION
    validate_publication(IMPLEMENTATION, LIMITS, EVIDENCE, "a" * 40, {"verificationPatch": tested})
    with pytest.raises(ValueError):
        validate_publication(IMPLEMENTATION, LIMITS, EVIDENCE, "a" * 40, {})
    with pytest.raises(ValueError):
        validate_publication(IMPLEMENTATION.replace("fixed", "untested"), LIMITS, EVIDENCE, "a" * 40, {"verificationPatch": tested})

def test_existing_tests_are_preserved_and_test_only_changes_rejected():
    existing = TEST.replace("new file mode 100644\n", "").replace("--- /dev/null", "--- a/test_generated.py").replace("@@ -0,0 +1 @@", "@@ -1 +1 @@")
    assert publication_patch(IMPLEMENTATION + existing) == IMPLEMENTATION + existing
    with pytest.raises(ValueError):
        publication_patch(TEST)
