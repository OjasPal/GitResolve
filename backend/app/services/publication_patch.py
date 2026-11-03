"""Separate newly added sandbox regression tests from the exact publishable patch."""
import re
from app.services.policy import validate_fix

def publication_patch(patch):
    sections = re.split(r"(?=^diff --git )", patch, flags=re.M)
    kept = []
    for section in sections:
        if not section.strip(): continue
        target = re.search(r"^\+\+\+ b/(.+)$", section, re.M)
        if not target: raise ValueError("Malformed patch section")
        name = target.group(1)
        is_test = name.split("/")[-1].startswith("test_") and name.endswith(".py")
        if is_test and re.search(r"^--- /dev/null$", section, re.M): continue
        kept.append(section)
    result = "".join(kept)
    if not result.strip(): raise ValueError("No implementation changes to publish")
    return result

def validate_publication(diff, limits, evidence, base_sha, receipt):
    tested = receipt.get("verificationPatch")
    if tested is not None:
        validate_fix(tested, limits, evidence, base_sha)
        if publication_patch(tested) != diff: raise ValueError("Publication differs from tested implementation")
        validate_fix(diff, limits, evidence, base_sha, require_regression=False)
    else:
        validate_fix(diff, limits, evidence, base_sha)
