"""Bounded repair orchestration. Repository code runs only in the Docker bridge."""
import ast
import difflib
import os
from pathlib import Path, PurePosixPath
import re
import time

from pydantic import BaseModel, Field, model_validator

from app.agent.interface import ExecuteResult
from app.schemas.enums import ArtifactType, EventLevel, EventType, FailureCode, TaskStage
from app.services.policy import is_allowed_config_profile, validate_fix
from app.core.redaction import redact
from app.worker.publication_text import PublicationText

DEFAULT_ADAPTIVE_LANES = "on"


def adaptive_lanes_enabled() -> bool:
    val = os.environ.get("ADAPTIVE_LANES", DEFAULT_ADAPTIVE_LANES).strip().lower()
    return val in {"1", "true", "on", "yes"}


class RepairProposal(BaseModel):
    explanation: str
    reproduction_diff: str = Field(description="Git unified diff adding only regression tests; those tests fail on the original source.")
    final_diff: str = Field(description="Complete git unified diff against the ORIGINAL source, including identical regression test sections and the implementation fix.")


class TextEdit(BaseModel):
    path: str
    old_text: str = Field(description="Exact unique original source text to replace. Empty only when adding a NEW file.")
    new_text: str


class RegressionTest(BaseModel):
    path: str = Field(description="A NEW Python test file whose basename begins test_.")
    content: str


class EditProposal(BaseModel):
    explanation: str
    tests: list[RegressionTest] = Field(default_factory=list, max_length=3)
    edits: list[TextEdit] = Field(default_factory=list, max_length=8)
    needed_files: list[str] = Field(default_factory=list, max_length=5, description="Repository-relative file paths or symbol names to inspect if required source context is missing from the prompt.")
    blocked_reason: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def actionable_or_blocked(self):
        if self.blocked_reason and self.blocked_reason.strip().lower() in {
            "none", "n/a", "null", "false", "no", "not blocked", "0", ""
        }:
            self.blocked_reason = ""
        if self.tests and self.edits:
            self.needed_files = []
            self.blocked_reason = ""
            return self
        if self.edits:
            self.needed_files = []
            self.blocked_reason = ""
            return self
        if self.blocked_reason or self.needed_files:
            if self.tests or self.edits:
                self.tests = []
                self.edits = []
            return self
        raise ValueError("An actionable repair requires tests and implementation edits.")


class FastEditProposal(BaseModel):
    explanation: str
    edits: list[TextEdit] = Field(default_factory=list, max_length=4)
    tests: list[RegressionTest] = Field(default_factory=list, max_length=3)
    escalate_reason: str = Field(default="", max_length=500)
    needed_files: list[str] = Field(default_factory=list, max_length=5)
    blocked_reason: str = Field(default="", max_length=1000)


class RequirementReview(BaseModel):
    requirement: str
    status: str = Field(pattern="^(passed|failed|not_verified)$")
    evidence: str = Field(min_length=1)


class QualityReview(PublicationText):
    approved: bool
    requirements: list[RequirementReview] = Field(min_length=1, max_length=15)
    blocking_issues: list[str]


from app.agent.frameworks import streamlit_entrypoint, adapter_for


def compile_edits(proposal: EditProposal, originals: dict[str, str]) -> RepairProposal:
    """Construct exact diffs as data; never run model code or write into a checkout."""
    def checked(name):
        path = PurePosixPath(name)
        if not name or path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name or any(c.isspace() for c in name):
            raise ValueError("Unsafe edit path.")
        return name

    def diff(name, before, after):
        if after and not after.endswith("\n"):
            after += "\n"
        lines = difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile="a/" + name if name in originals else "/dev/null", tofile="b/" + name)
        body = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)
        mode = "new file mode 100644\n" if name not in originals else ""
        return f"diff --git a/{name} b/{name}\n" + mode + body

    reproduction = ""
    used = set()
    for test in proposal.tests:
        name = checked(test.path)
        if name in originals or name in used or not PurePosixPath(name).name.startswith("test_") or not name.endswith(".py"):
            raise ValueError("Use a unique NEW regression test file.")
        used.add(name)
        reproduction += diff(name, "", test.content)
    targets = {}
    for edit in proposal.edits:
        name = checked(edit.path)
        if name in used:
            raise ValueError("Implementation edits cannot alter regression tests.")
        before = targets.get(name, originals.get(name, ""))
        if edit.old_text:
            if name not in originals or before.count(edit.old_text) != 1:
                if redact(before) != before:
                    raise ValueError(f"Edit for {name} must match original text exactly once. Use a short non-sensitive anchor; redacted source cannot be copied as a replacement target.")
                match = difflib.SequenceMatcher(None, edit.old_text, before).find_longest_match()
                start = max(0, match.b - match.a)
                actual = before[start:start + len(edit.old_text)]
                offset = next((i for i, (a, b) in enumerate(zip(edit.old_text, actual)) if a != b),
                              min(len(edit.old_text), len(actual)))
                hint = f" Proposed: {edit.old_text[max(0, offset-35):offset+65]!r}; actual source: {actual[max(0, offset-35):offset+65]!r}."
                raise ValueError(redact(f"Edit for {name} must match original text exactly once." + hint +
                                       " Use a short unique ASCII anchor; preserve exact Unicode/spacing."))
            targets[name] = before.replace(edit.old_text, edit.new_text, 1)
        else:
            if name in originals or name in targets:
                raise ValueError(f"Empty old_text is only allowed for a new file: {name}.")
            targets[name] = edit.new_text
    if any(name in originals and originals[name].strip() and not after.strip() for name,after in targets.items()):
        raise ValueError("Unsafe whole-file removal: use an explicitly approved cleanup plan instead of emptying an existing file.")
    final = reproduction + "".join(diff(name, originals.get(name, ""), after) for name, after in targets.items() if after != originals.get(name, ""))
    return RepairProposal(explanation=proposal.explanation, reproduction_diff=reproduction, final_diff=final)


def compile_fast_edits(proposal: FastEditProposal | EditProposal | RepairProposal, originals: dict[str, str]) -> str:
    """Compile implementation edits into a publishable unified diff without requiring LLM-written tests."""
    from app.services.publication_patch import publication_patch
    if isinstance(proposal, RepairProposal):
        return publication_patch(proposal.final_diff)
    if not proposal.edits:
        raise ValueError("Fast-lane proposal contains no implementation edits.")
    synthetic = EditProposal(
        explanation=proposal.explanation,
        tests=[RegressionTest(path="test_fast_placeholder.py", content="def test_placeholder():\n    assert True\n")],
        edits=proposal.edits,
    )
    compiled = compile_edits(synthetic, originals)
    return publication_patch(compiled.final_diff)


_CONTEXT_STOPWORDS = frozenset({
    "the", "and", "for", "not", "with", "from", "that", "this", "are", "was", "were", "will",
    "can", "should", "must", "need", "needs", "check", "where", "currently", "when", "into",
    "only", "use", "using", "def", "class", "return", "import", "true", "false", "none",
    "file", "files", "line", "lines", "code", "test", "tests", "fix", "issue", "bug", "error",
    "src", "app", "path", "instead", "have", "has", "had", "does", "did",
})


def _referenced_files(texts: list[str], originals: dict[str, str]) -> list[str]:
    joined = "\n".join(t for t in texts if t).replace("\\", "/")
    joined_lower = joined.lower()
    tokens = {
        tok for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", joined)
        if tok.lower() not in _CONTEXT_STOPWORDS
    }
    primary: list[str] = []
    secondary: list[str] = []
    for name in sorted(originals):
        base = PurePosixPath(name).name
        text = originals[name]
        if (
            name in joined
            or name.lower() in joined_lower
            or (len(base) > 3 and (base in joined or base.lower() in joined_lower))
        ):
            primary.append(name)
            continue
        if (name.startswith("tests/") or base.startswith("test_")) and (
            "tests/" in joined_lower or "test_" in joined_lower
        ):
            primary.append(name)
            continue
        if any(re.search(rf"\b(?:def|class|function|const)\s+{re.escape(tok)}\b", text) for tok in tokens):
            secondary.append(name)
    return primary + [n for n in secondary if n not in primary]


def _expand_context_for_files(base_context: str, originals: dict[str, str], files: list[str], max_extra_chars: int = 24000) -> str:
    if not files:
        return base_context
    # Prioritize files that were not already included in full in base_context
    prioritized: list[str] = []
    already_full: list[str] = []
    for name in files:
        if name not in originals:
            continue
        line_count = len(originals[name].splitlines())
        header_present = f"--- {name} (" in base_context
        last_line_present = line_count > 0 and f"\n{line_count:4}: " in base_context
        if header_present and last_line_present:
            already_full.append(name)
        else:
            prioritized.append(name)
    ordered_files = prioritized + already_full

    extra_chunks: list[str] = []
    remaining = max_extra_chars
    for name in ordered_files:
        if name not in originals or remaining < 100:
            continue
        lines = originals[name].splitlines()
        numbered = "\n".join(f"{i+1:4}: {line}" for i, line in enumerate(lines))
        cap = min(14000, remaining)
        if len(numbered) + 80 <= cap:
            block = f"\n--- {name} (requested full source; line numbered) ---\n" + redact(numbered)
        else:
            # Large file: include top lines plus sections matching issue/context keywords
            selected = set(range(min(60, len(lines))))
            for idx, line in enumerate(lines):
                low = line.lower()
                if any(k in low for k in ("webhook", "export", "dispatch", "error", "timeout", "exception", "fetch(")):
                    selected.update(range(max(0, idx - 6), min(len(lines), idx + 18)))
            excerpt = "\n".join(f"{i+1:4}: {lines[i]}" for i in sorted(selected))
            block = f"\n--- {name} (requested full source; line numbered) ---\n" + redact(excerpt)
        block = block[:cap]
        extra_chunks.append(block)
        remaining -= len(block)
    if not extra_chunks:
        return base_context
    return base_context + "\n\nAdditional requested repository files:" + "".join(extra_chunks)


def _blocked_outcome(reason: str) -> ExecuteResult:
    clean = redact(reason or "Additional clarification or narrower scope is required.")[:300]
    lower = clean.lower()
    is_context = any(
        phrase in lower
        for phrase in ("need to check", "need to see", "where ", "missing", "locate", "find ", "clarif", "unclear", "ambiguous", "context", "inspect")
    )
    is_scope = any(
        phrase in lower
        for phrase in ("scope", "limit", "maxdifffiles", "maxdifflines", "forbidden", "cannot fit", "outside", "too many files")
    )
    if is_scope and not is_context:
        return ExecuteResult(
            "out_of_scope",
            failure_code=FailureCode.NO_FIX_FOUND,
            failure_message=f"The requested change exceeds the current repair scope: {clean}",
            next_step="Narrow the requested change or update the plan scope before running again.",
        )
    return ExecuteResult(
        "needs_clarification",
        failure_code=FailureCode.ISSUE_AMBIGUOUS,
        failure_message=f"Additional context or clarification is needed to complete this repair safely: {clean}",
        next_step="Clarify the target file, function, and expected behavior in the issue description, then run again.",
    )


def repository_context(root: Path, max_chars: int = 60000, issue: str = "", focus_files: tuple[str, ...] | list[str] = ()) -> str:
    """Compact tree and ranked numbered excerpts; repository text stays untrusted."""
    from sandbox.profiler import source_files, profile_repository
    root = Path(root).resolve()
    profile = profile_repository(root)
    issue_norm = (issue or "").replace("\\", "/")
    focus_set = {p.replace("\\", "/").lstrip("./") for p in focus_files if p}
    all_tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", issue_norm))
    tokens = {tok for tok in all_tokens if tok.lower() not in _CONTEXT_STOPWORDS} or all_tokens
    candidates = []
    for path in source_files(root):
        name = path.relative_to(root).as_posix()
        if any(part.startswith(".") for part in Path(name).parts):
            continue
        if path.suffix not in {".py", ".toml", ".txt", ".md", ".html", ".css", ".json", ".ts", ".tsx", ".js", ".jsx"}:
            continue
        if any(word in path.name.lower() for word in ("secret", "credential", "private", "token", "service-account", "service_account")):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            continue
        base = PurePosixPath(name).name
        is_named = (
            name in focus_set
            or base in focus_set
            or (bool(issue_norm) and (name in issue_norm or (len(base) > 3 and base in issue_norm)))
        )
        lines = text.splitlines()
        def_hits = [
            i for i, line in enumerate(lines)
            if any(re.search(rf"\b(?:def|class|function|const)\s+{re.escape(tok)}\b", line) for tok in tokens)
        ]
        score = (
            500 * (name in focus_set or base in focus_set)
            + 200 * is_named
            + 150 * bool(def_hits)
            + 80 * (path.suffix == ".py")
            + 60 * base.startswith("test_")
            + 30 * (name in profile.entrypoints)
            + 20 * (name in profile.dependency_files)
        )
        score += sum(
            min(text.count(token), 5) * (3 if "_" in token or any(c.isupper() for c in token[1:]) else 1)
            for token in tokens
        )
        candidates.append((score, name, text, is_named, def_hits))
    tree = "Repository files:\n" + "\n".join(name for _, name, _, _, _ in sorted(candidates, key=lambda row: row[1]))
    chunks = [tree[:min(6000, max_chars // 4)]]
    remaining = max_chars - len(chunks[0])
    for _, name, text, is_named, def_hits in sorted(candidates, key=lambda row: (-row[0], row[1])):
        if remaining < 100:
            break
        lines = text.splitlines()
        is_test_file = PurePosixPath(name).name.startswith("test_")
        if len(lines) <= 100 or ((is_named or def_hits or is_test_file) and len(lines) <= 220):
            selected = set(range(len(lines)))
        else:
            selected = set(range(min(45 if is_named else 35, len(lines))))
            for d_hit in def_hits[:10]:
                selected.update(range(max(0, d_hit - 5), min(len(lines), d_hit + 45)))
            scored_hits = sorted(
                (
                    (
                        sum((3 if "_" in tok or any(c.isupper() for c in tok[1:]) else 1) for tok in tokens if tok in line),
                        i,
                    )
                    for i, line in enumerate(lines)
                    if any(token in line for token in tokens)
                ),
                key=lambda item: (-item[0], item[1]),
            )
            for _, hit in scored_hits[:20 if (is_named or def_hits) else 10]:
                selected.update(range(max(0, hit - 5), min(len(lines), hit + 15)))
        excerpt = "\n".join(f"{i+1:4}: {lines[i]}" for i in sorted(selected))
        block = f"\n--- {name} (untrusted source; line numbered) ---\n" + redact(excerpt)
        cap = 12000 if (is_named or def_hits) else 6000
        block = block[:min(cap, remaining)]
        chunks.append(block)
        remaining -= len(block)
    return "".join(chunks)


def diff_sections(diff: str) -> dict[str, str]:
    result = {}
    for section in re.split(r"(?=^diff --git )", diff, flags=re.MULTILINE):
        if not section.strip():
            continue
        target = re.search(r"^\+\+\+ b/(.+)$", section, re.MULTILINE)
        if not target or target[1] in result:
            raise ValueError("Patch must contain unique git diff sections with regular file targets.")
        result[target[1]] = section.rstrip() + "\n"
    return result


def validate_proposal(
    proposal,
    limits,
    base_sha,
    *,
    task_type: str = "bug",
    allowed_dependency_changes=None,
):
    # Validate paths and scope before a patch reaches the sandbox, independently of the model.
    effective_mode = (task_type or "bug").strip().lower()
    evidence_shape = {
        "reproduced": True,
        "regressionPassed": True,
        "testsRun": 1,
        "newFailures": [],
        "taskType": effective_mode,
        "baselinePassed": True,
        "touchedSymbolsCovered": True,
        "configProfileValidated": True,
    }
    if proposal.reproduction_diff or effective_mode in {"bug", "feature"}:
        validate_fix(
            proposal.reproduction_diff,
            limits,
            evidence_shape,
            base_sha,
            task_type=effective_mode,
            allowed_dependency_changes=allowed_dependency_changes,
        )
    validate_fix(
        proposal.final_diff,
        limits,
        evidence_shape,
        base_sha,
        require_regression=effective_mode in {"bug", "feature"},
        task_type=effective_mode,
        allowed_dependency_changes=allowed_dependency_changes,
    )
    repro = diff_sections(proposal.reproduction_diff)
    final = diff_sections(proposal.final_diff)
    for name, section in repro.items():
        if not PurePosixPath(name).name.startswith("test_") or not name.endswith(".py"):
            raise ValueError("Reproduction may change only regression test files.")
        if final.get(name) != section:
            raise ValueError("Regression tests must be identical before and after the fix.")
    if not set(final) - set(repro):
        raise ValueError("The final patch must include an implementation change.")


def passed(result):
    return result.status == "passed" and result.exit_code == 0 and not result.oom_killed and result.cleanup_ok


def output(result):
    from app.core.errors import safe_message
    return safe_message((result.stdout + "\n" + result.stderr)[-12000:])


def _declared_package_names(reqs) -> set[str]:
    items = reqs[0] if isinstance(reqs, tuple) and reqs else (reqs or [])
    names: set[str] = set()
    for req in items:
        if isinstance(req, str):
            m = re.match(r"^\s*([A-Za-z0-9_.-]+)", req)
            if m:
                names.add(m.group(1).casefold().replace("-", "_"))
    return names


def environment_outcome(category, result):
    from app.agent.lanes.external_guidance import extract_missing_packages

    user_action = category in {"missing_dep", "missing_secret", "invalid_dependency_spec", "collection_error"} or getattr(result, "error_code", "") == "unsupported_dependency"
    missing_pkgs = extract_missing_packages(result) if category == "missing_dep" or getattr(result, "error_code", "") == "unsupported_dependency" else []
    pkg_text = f" ({', '.join(missing_pkgs)})" if missing_pkgs else ""
    err_msg = getattr(result, "error_message", None)
    messages = {
        "missing_dep": err_msg or f"Required Python package{pkg_text} is missing from the repository dependency declarations or lacks a supported binary wheel.",
        "missing_secret": err_msg or "External service credentials are required by the repository tests or runtime configuration.",
        "collection_error": err_msg or "Pytest could not collect the repository test suite. Check module imports and test configuration.",
        "invalid_dependency_spec": err_msg or f"Declared dependency{pkg_text} requires a public binary wheel version.",
    }
    steps = {
        "missing_dep": f"Declare the required package{pkg_text} in requirements.txt or pyproject.toml using a binary-wheel-supported version.",
        "missing_secret": "Make tests independent of credentials by mocking external service boundaries or providing a safe test fixture. Never paste keys into the task.",
        "collection_error": "Fix test collection or clarify whether the reported bug is a collection failure before starting repair.",
        "invalid_dependency_spec": "Replace URL, VCS, local or source-build dependencies with supported public wheels.",
    }
    msg = messages.get(category) or err_msg or "The sandbox test environment could not complete verification."
    return ExecuteResult("needs_user_action" if user_action else "infra_retry",
        failure_code=FailureCode.SANDBOX_ERROR,
        failure_message=msg,
        evidence=["final_tests"], next_step=steps.get(category, "Retry after the execution environment is available."))


def run_repair(task, ctx, router=None, *, engine=None, checkpointer=None, **kwargs):
    from app.agent.graph_engine import (
        CHECKPOINT_SCHEMA_VERSION,
        CheckpointCompatibilityError,
        SqliteRepairCheckpointer,
        get_engine_mode,
        make_thread_id,
        run_repair_graph,
    )
    mode = get_engine_mode(engine)
    if mode != "graph":
        if checkpointer is not None or os.environ.get("GITRESOLVE_SQLITE_CHECKPOINT_PATH", "").strip():
            cp = checkpointer or SqliteRepairCheckpointer()
            try:
                cp.pin_or_verify_thread(
                    make_thread_id(task.task_id, "execute"),
                    engine_mode=mode,
                    base_sha=getattr(task, "base_sha", "") or "",
                    schema_version=CHECKPOINT_SCHEMA_VERSION,
                )
            except (CheckpointCompatibilityError, ValueError) as exc:
                return ExecuteResult(
                    "infra_retry",
                    failure_code=FailureCode.INTERNAL_ERROR,
                    failure_message=f"Checkpoint pinned engine mismatch: {redact(str(exc), 200)}",
                )
        return ExecuteResult(
            "infra_retry",
            failure_code=FailureCode.INTERNAL_ERROR,
            failure_message="AGENT_ENGINE=legacy has been retired in Phase 2B; use AGENT_ENGINE=graph.",
        )
    return run_repair_graph(task, ctx, router=router, checkpointer=checkpointer, **kwargs)
