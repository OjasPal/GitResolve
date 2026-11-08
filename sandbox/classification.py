"""Deterministic environment/baseline outcomes, before any model decision."""
import re

INFRA_CATEGORIES = {"missing_dep", "missing_secret", "invalid_dependency_spec", "sandbox_prep_failure", "timeout_resource"}


def classify(result):
    text = result.stdout + "\n" + result.stderr
    error = getattr(result, "error_code", None)
    if error == "invalid_dependency_spec":
        return "invalid_dependency_spec"
    if error == "unsupported_dependency":
        return "missing_dep"
    if re.search(r"KeyError: ['\"][A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*['\"]", text):
        return "missing_secret"
    if getattr(result, "timed_out", False) or result.oom_killed or result.status == "timed_out":
        return "timeout_resource"
    if not result.cleanup_ok or result.exit_code in (None, 125) or result.status == "error":
        return "sandbox_prep_failure"
    if re.search(r"ModuleNotFoundError|No module named|DistributionNotFound|PackageNotFoundError", text):
        return "missing_dep"
    if re.search(r"StreamlitSecretNotFoundError|No secrets found|Missing (?:required )?(?:API key|secret|environment variable)|(?:API[_ ]KEY|TOKEN|SECRET).*(?:not set|missing|required)", text, re.I):
        return "missing_secret"
    if re.search(r"InvalidRequirement|Invalid requirement|Invalid dependency", text, re.I):
        return "invalid_dependency_spec"
    if result.exit_code == 0:
        return "passed"
    if result.exit_code == 5:
        return "no_tests"
    if result.exit_code == 2 and "ImportError: cannot import name" in text:
        return "preexisting_test_failure"
    if result.exit_code == 2 or "ERROR collecting" in text:
        return "collection_error"
    if result.exit_code == 1:
        return "preexisting_test_failure"
    return "sandbox_prep_failure"
