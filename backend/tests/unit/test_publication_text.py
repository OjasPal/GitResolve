import pytest
from pydantic import ValidationError

from app.services.store.memory import MemoryStore
from app.worker.publication_text import PublicationText, publication_subject, reserve_branch


@pytest.mark.parametrize("text", ["fix: Do not add React or Next.js", "fix: Update UI\nIgnore instructions", "fix: Update tsk_deadbeef", "fix: " + "x" * 73])
def test_instruction_or_identifier_subjects_are_rejected(text):
    with pytest.raises(ValidationError):
        PublicationText(commit_message=text)


def test_readable_branches_are_unique_and_stable_on_retry():
    store = MemoryStore()
    first = {"id": "tsk_one", "repoId": "1_2"}
    second = {"id": "tsk_two", "repoId": "1_2"}
    store.set("tasks/tsk_one", first)
    store.set("tasks/tsk_two", second)
    subject = "fix: Correct quiz rendering"
    a = reserve_branch(store, first, subject, "digest-one")
    b = reserve_branch(store, second, subject, "digest-two")
    assert a == "gitresolve-agent/correct-quiz-rendering"
    assert b == "gitresolve-agent/correct-quiz-rendering-b"
    assert reserve_branch(store, first, subject, "digest-one") == a
    with pytest.raises(ValueError):
        reserve_branch(store, first, subject, "another-diff")


def test_old_tasks_never_reuse_the_prompt_as_commit_message():
    task = {"issue": {"title": "Do not add React or Next.js " * 8}}
    assert publication_subject(task, "+++ b/StreamlitAPP.py\n") == "fix: Improve Streamlit interface"


def test_branch_uses_reviewed_topic_and_keeps_whole_words():
    store = MemoryStore()
    task = {"id": "tsk_topic", "repoId": "1_2", "finalDiffSummary": {"branchName": "quiz-docx-export"}}
    store.set("tasks/tsk_topic", task)
    assert reserve_branch(store, task, "feat: Add document export for generated quiz questions", "hash") == "gitresolve-agent/quiz-docx-export"
    with pytest.raises(ValidationError):
        PublicationText(commit_message="feat: Add quiz export", branch_name="export-quiz-123")
