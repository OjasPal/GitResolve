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

def validate_publication(diff, limits, evidence, base_sha, receipt, cleanup_scope=None):
    if cleanup_scope:
        from sandbox.cleanup import validate_cleanup_patch, validate_scope
        names=validate_scope(cleanup_scope,base_sha)
        validate_cleanup_patch(diff,cleanup_scope)
        if receipt.get("cleanupVerified") is not True or receipt.get("allowedDeletions") != names:
            raise ValueError("Cleanup requires trusted deletion and compatibility checks.")
        validate_fix(diff,limits,evidence,base_sha,require_regression=False,allowed_deletions=names)
        return
    if evidence.get("lane") == "fast" and evidence.get("verificationLevel") in {"V0", "V1"}:
        from app.services.ids import sha256_hash
        oracle_receipt = receipt.get("staticOracleReceipt") if isinstance(receipt, dict) else None
        if receipt:
            if (
                receipt.get("source") != "docker"
                or receipt.get("passed") is not True
                or receipt.get("baseSha") != base_sha
                or receipt.get("diffHash") != sha256_hash(diff)
                or not isinstance(oracle_receipt, dict)
                or oracle_receipt.get("verified") is not True
                or oracle_receipt.get("originalFailed") is not True
                or oracle_receipt.get("patchedPassed") is not True
                or oracle_receipt.get("baseSha") != base_sha
                or oracle_receipt.get("diffHash") != sha256_hash(diff)
            ):
                raise ValueError("Fast-lane static verification requires a trusted Docker oracle receipt for the exact base SHA and diff hash.")
        validate_fix(diff, limits, evidence, base_sha, require_regression=False, allow_static_oracle=True)
        return
    tested = receipt.get("verificationPatch")
    if tested is not None:
        validate_fix(tested, limits, evidence, base_sha)
        if publication_patch(tested) != diff: raise ValueError("Publication differs from tested implementation")
        validate_fix(diff, limits, evidence, base_sha, require_regression=False)
    else:
        validate_fix(diff, limits, evidence, base_sha)
