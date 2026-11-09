"""Separate newly added sandbox regression tests from the exact publishable patch."""
import re
from app.services.policy import HARD_CAPS, validate_fix


def publication_patch(patch):
    sections = re.split(r"(?=^diff --git )", patch, flags=re.M)
    kept = []
    for section in sections:
        if not section.strip():
            continue
        target = re.search(r"^\+\+\+ b/(.+)$", section, re.M)
        if not target:
            raise ValueError("Malformed patch section")
        name = target.group(1)
        is_test = name.split("/")[-1].startswith("test_") and name.endswith(".py")
        if is_test and re.search(r"^--- /dev/null$", section, re.M):
            continue
        kept.append(section)
    result = "".join(kept)
    if not result.strip():
        raise ValueError("No implementation changes to publish")
    return result


def _count_stripped_test_budget(tested_patch: str, pub_patch: str) -> tuple[int, int]:
    """Count newly added sandbox test files and lines stripped by publication_patch."""
    tested_sections = [s for s in re.split(r"(?=^diff --git )", tested_patch or "", flags=re.M) if s.strip()]
    pub_sections = [s for s in re.split(r"(?=^diff --git )", pub_patch or "", flags=re.M) if s.strip()]
    extra_files = max(0, len(tested_sections) - len(pub_sections))
    extra_lines = 0
    if extra_files > 0:
        for sec in tested_sections:
            target = re.search(r"^\+\+\+ b/(.+)$", sec, re.M)
            if not target:
                continue
            name = target.group(1)
            is_test = name.split("/")[-1].startswith("test_") and name.endswith(".py")
            if is_test and re.search(r"^--- /dev/null$", sec, re.M):
                for ln in sec.splitlines():
                    if ln.startswith(("+++ ", "--- ")):
                        continue
                    if ln.startswith(("+", "-")):
                        extra_lines += 1
    return extra_files, extra_lines


def validate_publication(
    diff,
    limits,
    evidence,
    base_sha,
    receipt,
    cleanup_scope=None,
    *,
    task_type: str | None = None,
    allowed_dependency_changes=None,
):
    effective_mode = str(task_type or (evidence.get("taskType") if isinstance(evidence, dict) else None) or "bug").strip().lower()
    effective_deps = (
        allowed_dependency_changes
        if allowed_dependency_changes is not None
        else (evidence.get("approvedDependencyChanges") if isinstance(evidence, dict) else None)
    )

    if cleanup_scope:
        from sandbox.cleanup import validate_cleanup_patch, validate_scope
        names = validate_scope(cleanup_scope, base_sha)
        validate_cleanup_patch(diff, cleanup_scope)
        if receipt.get("cleanupVerified") is not True or receipt.get("allowedDeletions") != names:
            raise ValueError("Cleanup requires trusted deletion and compatibility checks.")
        validate_fix(
            diff,
            limits,
            evidence,
            base_sha,
            require_regression=False,
            allowed_deletions=names,
            task_type=effective_mode,
            allowed_dependency_changes=effective_deps,
        )
        return
    if effective_mode == "bug" and evidence.get("lane") == "fast" and evidence.get("verificationLevel") in {"V0", "V1"}:
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
                raise ValueError(
                    "Fast-lane static verification requires a trusted Docker oracle receipt for the exact base SHA and diff hash."
                )
        validate_fix(
            diff,
            limits,
            evidence,
            base_sha,
            require_regression=False,
            allow_static_oracle=True,
            task_type=effective_mode,
            allowed_dependency_changes=effective_deps,
        )
        return
    tested = receipt.get("verificationPatch") if isinstance(receipt, dict) else None
    effective_limits = dict(limits)
    if effective_mode in {"bug", "feature", "refactor"} and int(effective_limits.get("maxDiffLines", 150)) < 120:
        effective_limits["maxDiffLines"] = min(HARD_CAPS["maxDiffLines"], max(120, int(effective_limits["maxDiffLines"])))
    if tested is not None:
        if publication_patch(tested) != diff:
            raise ValueError("Publication differs from tested implementation")
        extra_files, extra_lines = _count_stripped_test_budget(tested, diff)
        tested_limits = dict(effective_limits)
        if extra_files > 0:
            tested_limits["maxDiffFiles"] = min(
                HARD_CAPS["maxDiffFiles"], int(effective_limits["maxDiffFiles"]) + extra_files
            )
            tested_limits["maxDiffLines"] = min(
                HARD_CAPS["maxDiffLines"], int(effective_limits["maxDiffLines"]) + extra_lines
            )
        validate_fix(
            tested,
            tested_limits,
            evidence,
            base_sha,
            task_type=effective_mode,
            allowed_dependency_changes=effective_deps,
        )
        validate_fix(
            diff,
            effective_limits,
            evidence,
            base_sha,
            require_regression=False,
            task_type=effective_mode,
            allowed_dependency_changes=effective_deps,
        )
    else:
        validate_fix(
            diff,
            effective_limits,
            evidence,
            base_sha,
            require_regression=effective_mode in {"bug", "feature"},
            task_type=effective_mode,
            allowed_dependency_changes=effective_deps,
        )
