"""Rehydratable in-memory workspace overlay for Phase 2A LangGraph repair execution."""
from __future__ import annotations

import difflib
import hashlib
from pathlib import Path, PurePosixPath
from typing import Any

from app.core.redaction import redact
from app.services.policy import ALLOWED_CONFIG_PROFILES, is_allowed_requirements_file

MAX_OVERLAY_FILE_BYTES = 100_000
_PROTECTED_FILES = frozenset({
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Dockerfile",
    "Jenkinsfile",
    "Makefile",
})
_PROTECTED_SUFFIXES = (".lock", ".yaml", ".yml")
_PROTECTED_PREFIXES = (".github/", "infra/", "terraform/", "k8s/", "helm/", "migrations/")
_SECRET_NAME_TOKENS = (
    ".env",
    "secret",
    "credential",
    "private_key",
    "id_rsa",
    "service-account",
    "service_account",
)
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx")


def load_repository_originals(root: Path) -> dict[str, str]:
    """Load non-hidden, non-symlink text files <=100KB from a cloned checkout into memory."""
    originals: dict[str, str] = {}
    root = Path(root).resolve()
    for path in root.rglob("*"):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            if path.stat().st_size > MAX_OVERLAY_FILE_BYTES:
                continue
            rel = path.relative_to(root).as_posix()
        except OSError:
            continue
        parts = PurePosixPath(rel).parts
        is_dotenv_example = (
            parts[-1].lower() in {".env.example", ".env.sample", ".env.template"}
            and not any(part.startswith(".") for part in parts[:-1])
        )
        if any(part.startswith(".") for part in parts):
            if not is_dotenv_example and not (len(parts) == 1 and parts[0] in ALLOWED_CONFIG_PROFILES):
                continue
        try:
            text = path.read_text(encoding="utf-8")
            originals[rel] = redact(text) if is_dotenv_example else text
        except (UnicodeError, OSError):
            continue
    return originals


def validate_safe_repo_path(
    raw_path: str,
    *,
    root: Path | None = None,
    for_write: bool = False,
    allow_requirements: bool = False,
) -> tuple[bool, str, str]:
    """Validate that a tool path stays inside the repository and avoids protected/secret files."""
    if not raw_path or not isinstance(raw_path, str):
        return False, "", "Path must be a non-empty relative POSIX path."
    norm = raw_path.strip().replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    pure = PurePosixPath(norm)
    if (
        not norm
        or pure.is_absolute()
        or ".." in pure.parts
        or ":" in norm
        or any(c.isspace() for c in norm)
    ):
        return False, "", f"Unsafe or traversal path rejected: {raw_path!r}"
    if any(part == ".git" for part in pure.parts):
        return False, "", "Access to .git metadata is forbidden."

    lower_name = pure.name.lower()
    lower_norm = norm.lower()
    if lower_name == ".env" or lower_name.startswith(".env.") or lower_name.endswith(_SECRET_SUFFIXES):
        return False, "", f"Access to secret or key file is forbidden: {norm}"
    if any(tok in lower_name for tok in _SECRET_NAME_TOKENS):
        return False, "", f"Access to credential/secret file is forbidden: {norm}"

    if for_write:
        req_blocked = pure.name.startswith("requirements") and not (
            allow_requirements and is_allowed_requirements_file(norm)
        )
        if req_blocked:
            return False, "", f"Unapproved dependency file modification is forbidden by policy: {norm}"
        if (
            pure.name in _PROTECTED_FILES
            or pure.suffix in _PROTECTED_SUFFIXES
            or any(lower_norm.startswith(p) for p in _PROTECTED_PREFIXES)
            or "/migrations/" in lower_norm
        ):
            return False, "", f"Modifying protected path is forbidden by policy: {norm}"

    if root is not None:
        resolved_root = Path(root).resolve()
        candidate = resolved_root / norm
        try:
            if candidate.is_symlink():
                return False, "", f"Symlink target rejected: {norm}"
            for parent in candidate.parents:
                if parent == resolved_root:
                    break
                if parent.is_symlink():
                    return False, "", f"Symlink parent directory rejected: {norm}"
            resolved_candidate = candidate.resolve()
            resolved_candidate.relative_to(resolved_root)
        except (OSError, ValueError):
            return False, "", f"Path escapes repository root: {norm}"

    return True, norm, ""


def _unified_file_diff(name: str, before: str | None, after: str) -> str:
    existed = before is not None
    before_text = before or ""
    if after and not after.endswith("\n"):
        after += "\n"
    lines = difflib.unified_diff(
        before_text.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{name}" if existed else "/dev/null",
        tofile=f"b/{name}",
    )
    body = "".join(
        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
        for line in lines
    )
    if not body:
        return ""
    mode = "" if existed else "new file mode 100644\n"
    return f"diff --git a/{name} b/{name}\n{mode}{body}"


class WorkspaceOverlay:
    """In-memory workspace overlay over immutable repository originals.

    Can be serialized into compact `overlay_edits` and `overlay_tests` records
    in `RepairState` and rehydrated after a checkpoint resume.
    """

    def __init__(
        self,
        base_sha: str,
        originals: dict[str, str],
        *,
        root: Path | None = None,
        allow_requirements: bool = False,
    ) -> None:
        self.base_sha = base_sha
        self.originals = dict(originals)
        self.root = Path(root).resolve() if root is not None else None
        self.allow_requirements = allow_requirements
        self._modified_sources: dict[str, str] = {}
        self._edit_records: list[dict[str, Any]] = []
        self._test_files: dict[str, str] = {}

    @classmethod
    def from_state(
        cls,
        *,
        base_sha: str,
        originals: dict[str, str],
        overlay_edits: list[dict[str, Any]] | None = None,
        overlay_tests: list[dict[str, str]] | None = None,
        expected_diff_hash: str = "",
        root: Path | None = None,
        allow_requirements: bool = False,
    ) -> "WorkspaceOverlay":
        overlay = cls(
            base_sha=base_sha,
            originals=originals,
            root=root,
            allow_requirements=allow_requirements,
        )
        for item in overlay_edits or []:
            path = str(item.get("path", ""))
            old_text = str(item.get("old_text", ""))
            new_text = str(item.get("new_text", ""))
            start_line = item.get("start_line")
            end_line = item.get("end_line")
            if not old_text and path not in originals and path not in overlay._modified_sources:
                ok, msg = overlay.create_file(path, new_text, is_test=False)
            else:
                ok, msg = overlay.apply_edit(
                    path,
                    old_text=old_text,
                    new_text=new_text,
                    start_line=start_line,
                    end_line=end_line,
                )
            if not ok:
                raise ValueError(f"Failed to rehydrate overlay edit on {path}: {msg}")

        for test_item in overlay_tests or []:
            tpath = str(test_item.get("path", ""))
            tcontent = str(test_item.get("content", ""))
            ok, msg = overlay.create_file(tpath, tcontent, is_test=True)
            if not ok:
                raise ValueError(f"Failed to rehydrate overlay test {tpath}: {msg}")

        if expected_diff_hash and overlay.diff_hash() != expected_diff_hash:
            raise ValueError(
                f"Rehydrated workspace diff hash mismatch: expected {expected_diff_hash}, got {overlay.diff_hash()}"
            )
        return overlay

    def current_files(self) -> dict[str, str]:
        merged = dict(self.originals)
        merged.update(self._modified_sources)
        merged.update(self._test_files)
        return merged

    def read_file_content(self, path: str) -> tuple[bool, str, str]:
        ok, norm, err = validate_safe_repo_path(path, root=self.root, for_write=False)
        if not ok:
            return False, "", err
        files = self.current_files()
        if norm not in files:
            return False, "", f"File not found in repository: {norm}"
        return True, files[norm], ""

    def apply_edit(
        self,
        path: str,
        old_text: str,
        new_text: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> tuple[bool, str]:
        """Apply an exact, unique whitespace-tolerant, or validated line-range edit."""
        ok, norm, err = validate_safe_repo_path(
            path,
            root=self.root,
            for_write=True,
            allow_requirements=self.allow_requirements,
        )
        if not ok:
            return False, err

        is_test_target = norm in self._test_files
        current_map = self._test_files if is_test_target else self._modified_sources
        before = current_map.get(norm, self.originals.get(norm))
        if before is None:
            return False, f"Cannot edit non-existent file {norm}; use create_file for new files."

        matched_old = ""
        updated: str | None = None

        # 1. Exact substring match (global or within start_line..end_line window)
        if old_text:
            count = before.count(old_text)
            if count == 1:
                matched_old = old_text
                updated = before.replace(old_text, new_text, 1)
            elif count > 1 and start_line is not None and end_line is not None:
                lines = before.splitlines(keepends=True)
                s_idx = max(0, int(start_line) - 1)
                e_idx = min(len(lines), int(end_line))
                window = "".join(lines[s_idx:e_idx])
                if window.count(old_text) == 1:
                    replaced_window = window.replace(old_text, new_text, 1)
                    updated = "".join(lines[:s_idx]) + replaced_window + "".join(lines[e_idx:])
                    matched_old = old_text
                else:
                    return False, f"Ambiguous edit on {norm}: old_text appears {count} times in file and {window.count(old_text)} times in lines {start_line}..{end_line}."
            elif count > 1:
                return False, f"Ambiguous edit on {norm}: old_text appears {count} times. Provide more surrounding context or start_line/end_line."

        # 2. Unique whitespace-tolerant line-block match
        if updated is None and old_text:
            src_lines = before.splitlines(keepends=True)
            needle_lines = [ln.strip() for ln in old_text.splitlines() if ln.strip() or len(old_text.splitlines()) == 1]
            if needle_lines:
                n_len = len(old_text.splitlines())
                raw_needle = [ln.rstrip() for ln in old_text.splitlines()]
                matches: list[tuple[int, int]] = []
                for idx in range(0, len(src_lines) - n_len + 1):
                    candidate = [ln.rstrip() for ln in src_lines[idx : idx + n_len]]
                    if candidate == raw_needle or [c.strip() for c in candidate] == [r.strip() for r in raw_needle]:
                        matches.append((idx, idx + n_len))
                if len(matches) == 1:
                    s_idx, e_idx = matches[0]
                    matched_old = "".join(src_lines[s_idx:e_idx])
                    replacement_chunk = new_text
                    if matched_old.endswith("\n") and replacement_chunk and not replacement_chunk.endswith("\n"):
                        replacement_chunk += "\n"
                    updated = "".join(src_lines[:s_idx]) + replacement_chunk + "".join(src_lines[e_idx:])
                elif len(matches) > 1:
                    return False, f"Ambiguous whitespace-tolerant match on {norm}: {len(matches)} candidate blocks match."

        # 3. Validated line-range replacement
        if updated is None and start_line is not None and end_line is not None:
            src_lines = before.splitlines(keepends=True)
            if not (1 <= int(start_line) <= int(end_line) <= max(1, len(src_lines))):
                return False, f"Invalid line range {start_line}..{end_line} for {norm} ({len(src_lines)} lines)."
            s_idx = int(start_line) - 1
            e_idx = int(end_line)
            slice_text = "".join(src_lines[s_idx:e_idx])
            if not old_text or old_text.strip() in slice_text.strip():
                matched_old = slice_text
                replacement_chunk = new_text
                if slice_text.endswith("\n") and replacement_chunk and not replacement_chunk.endswith("\n"):
                    replacement_chunk += "\n"
                updated = "".join(src_lines[:s_idx]) + replacement_chunk + "".join(src_lines[e_idx:])

        if updated is None:
            return False, f"Edit target did not match {norm}. Read the target lines first with read_file to inspect exact text."

        if norm in self.originals and self.originals[norm].strip() and not updated.strip():
            return False, f"Unsafe whole-file emptying rejected for {norm}."
        if len(updated.encode("utf-8")) > MAX_OVERLAY_FILE_BYTES:
            return False, f"Edited file {norm} exceeds 100KB limit."
        if redact(new_text) != new_text:
            return False, f"Edit for {norm} contains redacted secret or host-path patterns."

        if is_test_target:
            self._test_files[norm] = updated
        else:
            self._modified_sources[norm] = updated
            self._edit_records.append({
                "path": norm,
                "old_text": matched_old,
                "new_text": new_text,
                **(
                    {"start_line": int(start_line), "end_line": int(end_line)}
                    if start_line is not None and end_line is not None
                    else {}
                ),
            })
        return True, f"Updated {norm} ({len(updated.splitlines())} lines)."

    def create_file(
        self,
        path: str,
        content: str,
        *,
        is_test: bool | None = None,
    ) -> tuple[bool, str]:
        """Create a new source or regression test file in the overlay."""
        ok, norm, err = validate_safe_repo_path(
            path,
            root=self.root,
            for_write=True,
            allow_requirements=self.allow_requirements,
        )
        if not ok:
            return False, err
        if len(content.encode("utf-8")) > MAX_OVERLAY_FILE_BYTES:
            return False, f"New file {norm} exceeds 100KB limit."
        if redact(content) != content:
            return False, f"New file {norm} contains secret or host-path patterns."

        pure = PurePosixPath(norm)
        auto_is_test = is_test if is_test is not None else (
            pure.name.startswith("test_") and pure.name.endswith(".py") and norm not in self.originals
        )
        if auto_is_test:
            if norm in self.originals:
                return False, f"Regression test {norm} must be a NEW file not already in originals."
            if not (pure.name.startswith("test_") and pure.name.endswith(".py")):
                return False, f"Regression test file {norm} must be named test_*.py."
            self._test_files[norm] = content if content.endswith("\n") else content + "\n"
            return True, f"Created sandbox regression test {norm} ({len(content.splitlines())} lines)."

        if norm in self.originals:
            return False, f"File {norm} already exists in originals; use edit_file instead of create_file."
        self._modified_sources[norm] = content if content.endswith("\n") else content + "\n"
        self._edit_records.append({
            "path": norm,
            "old_text": "",
            "new_text": self._modified_sources[norm],
        })
        return True, f"Created source file {norm} ({len(content.splitlines())} lines)."

    def serialize_edits(self) -> list[dict[str, Any]]:
        return list(self._edit_records)

    def serialize_tests(self) -> list[dict[str, str]]:
        return [{"path": k, "content": v} for k, v in sorted(self._test_files.items())]

    def implementation_diff(self) -> str:
        chunks: list[str] = []
        for name in sorted(self._modified_sources):
            after = self._modified_sources[name]
            before = self.originals.get(name)
            if before == after:
                continue
            d = _unified_file_diff(name, before, after)
            if d:
                chunks.append(d)
        return "".join(chunks)

    def reproduction_diff(self) -> str:
        chunks: list[str] = []
        for name in sorted(self._test_files):
            d = _unified_file_diff(name, None, self._test_files[name])
            if d:
                chunks.append(d)
        return "".join(chunks)

    def combined_diff(self) -> str:
        return self.reproduction_diff() + self.implementation_diff()

    def diff_hash(self) -> str:
        impl = self.implementation_diff()
        if not impl:
            return ""
        return hashlib.sha256(impl.encode("utf-8")).hexdigest()
