"""Human-facing publication text; task identifiers stay in internal records."""
import re
import unicodedata

from pydantic import BaseModel, Field, field_validator
from app.core.redaction import redact


class PublicationText(BaseModel):
    commit_message: str = Field(min_length=10, max_length=72, description="One imperative conventional-commit subject describing the implemented change. No prompt instructions, task IDs, hashes or Markdown.")

    @field_validator("commit_message")
    @classmethod
    def concise_subject(cls, text):
        if (text != text.strip() or any(c in text for c in "\r\n\t`")
                or not re.fullmatch(r"(?:fix|feat|refactor|style|test|docs|chore)(?:\([a-z0-9-]+\))?: [^\x00-\x1f]+", text)
                or re.search(r"(?i)\b(?:do not|don't|you must|you should|please|tsk_[a-z0-9]+)\b|https?://", text)
                or redact(text) != text):
            raise ValueError("Commit subject must briefly describe the completed change, without instructions or identifiers.")
        return text


def publication_subject(task, diff):
    candidate = (task.get("finalDiffSummary") or {}).get("commitMessage")
    if candidate:
        return PublicationText(commit_message=candidate).commit_message
    # Compatibility for tasks completed before LLM publication metadata existed.
    paths = re.findall(r"^\+\+\+ b/(.+)$", diff, re.M)
    if any("streamlit" in path.lower() for path in paths):
        return "fix: Improve Streamlit interface"
    names = [path.rsplit("/", 1)[-1].rsplit(".", 1)[0] for path in paths if not path.rsplit("/", 1)[-1].startswith("test_")]
    return "fix: Update " + (", ".join(names)[:56] or "repository implementation")


def reserve_branch(store, task, subject, digest):
    slug = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", subject.split(": ", 1)[-1]).encode("ascii", "ignore").decode().lower()).strip("-")[:42].rstrip("-") or "repository-update"
    def reserve(tx):
        current = tx.get("tasks/" + task["id"])
        counter_path = f"repositories/{task['repoId']}/counters/publication"
        counter = tx.get(counter_path) or {}
        existing = current.get("publication") or {}
        if existing:
            if existing["diffHash"] != digest:
                raise ValueError("Publication reservation belongs to a different diff.")
            return existing["branch"]
        number = counter.get("nextNumber", 1)
        branch = f"gitresolve-agent/{slug}-{number}"
        tx.set(counter_path, {"nextNumber": number + 1})
        tx.update("tasks/" + task["id"], {"publication": {"branch": branch, "diffHash": digest, "commitMessage": subject}})
        return branch
    return store.run_transaction(reserve)
