"""Read-only diagnostic: prove the merged MCQGen rendering fails the new UI gate."""
import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]
from app.core.config import Settings
from app.services.store.memory import MemoryStore
from app.services.github_real import RealGitHubGateway
from sandbox.models import ExecutionRequest
from sandbox.runner import run_sandbox
from app.agent.repair_runner import QualityReview, repository_context
from sandbox.streamlit_gate import REPORT_SAMPLE

TEST = '''import sys
import types
from streamlit.testing.v1 import AppTest

def test_actual_app_answer_toggle(monkeypatch):
    boundary = types.ModuleType("src.mcqgenerator.MCQGenerator")
    calls = []
    def invoke(*args, **kwargs):
        calls.append(1)
        raise AssertionError("Model must not be called when toggling answers")
    boundary.generate_evaluate_chain = types.SimpleNamespace(invoke=invoke)
    monkeypatch.setitem(sys.modules, boundary.__name__, boundary)
    app = AppTest.from_file("StreamlitAPP.py")
    questions = [{"question": "A long representative question about safe and readable quiz output?", "options": [("a", "First option"), ("b", "Second option"), ("c", "Third option"), ("d", "Fourth option")], "correct": "b"}]
    app.session_state["quiz_items"] = questions
    app.session_state["quiz_review"] = __REPORT_SAMPLE__
    app.run(timeout=15)
    assert not app.exception
    assert app.checkbox[0].value is False
    app.checkbox[0].check().run(timeout=15)
    assert not app.exception
    assert app.session_state["quiz_items"] == questions
    assert calls == []
'''.replace("__REPORT_SAMPLE__", repr(REPORT_SAMPLE))


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    settings = Settings()
    store = MemoryStore(settings.local_state_path)
    task = next(t for t in store._docs.values() if isinstance(t, dict) and t.get("repoFullName", "").lower() == "ojaspal/mcqgen" and "userId" in t)
    gateway = RealGitHubGateway(settings, store)
    sha = gateway.resolve(task)
    with tempfile.TemporaryDirectory(prefix="gitresolve-ui-check-") as td:
        workspace = Path(td)
        source = gateway.source(task)(SimpleNamespace(repo_url="https://github.com/" + task["repoFullName"], commit_sha=sha), workspace, time.monotonic()+90, None)
        (source / "test_gitresolve_actual_ui.py").write_text(TEST, encoding="utf-8")
        context = repository_context(source, 40000)
        request = ExecutionRequest(execution_id="merged-ui-check", repo_url="https://github.com/" + task["repoFullName"],
                                   commit_sha=sha, command=["python", "-m", "pytest", "-q", "-p", "gitresolve_streamlit_gate"],
                                   timeout_seconds=90, profile="streamlit", ui_entrypoint="StreamlitAPP.py",
                                   require_ui_checks=True, require_ui_interaction=True, require_ui_report=True, require_ui_answers=True)
        result = run_sandbox(request, source_provider=lambda *a: source)
        text = result.model_dump_json(indent=2)
        (ROOT / ".diagnostics" / "merged-ui-check.json").write_text(text, encoding="utf-8")
        print(result.stdout)
        print(result.stderr[-2000:])
        print("RESULT", result.status, result.exit_code, result.cleanup_ok)
        if result.exit_code != 1 or "UI GATE FAILED" not in result.stdout or "HTML as code" not in result.stdout:
            raise SystemExit("The diagnostic did not establish the expected rendering rejection.")
        if "--review" in sys.argv:
            from agent.router import get_router
            response = get_router().route_normal(
                "Critically review the current MCQGen implementation against this request: clean black glassmorphism Streamlit UI, "
                "readable questions with four options, answers hidden by default and revealed next to their question by checkbox "
                "without model re-generation, and a readable evaluation report. Assess the actual source and test evidence, "
                "not the patch author's claims. A failed framework rendering gate must block approval, even if helper tests pass. "
                "Return QualityReview with concrete findings and a concise conventional commit subject describing the changes "
                "already implemented, never user instructions, task IDs or hashes. No browser/visual verification was performed.\n"
                + context + "\nACTUAL DOCKER UI EVIDENCE\n" + result.stdout, schema=QualityReview)
            review = response.result.parsed
            (ROOT / ".diagnostics" / "live-quality-review.json").write_text(review.model_dump_json(indent=2), encoding="utf-8")
            print("LIVE REVIEW APPROVED", review.approved)
            print("GENERATED COMMIT SUBJECT", review.commit_message)
            print("BLOCKING ISSUES", review.blocking_issues)
            if review.approved:
                raise SystemExit("Reviewer incorrectly approved the broken rendering.")


if __name__ == "__main__":
    main()
