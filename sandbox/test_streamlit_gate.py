import subprocess

import pytest

from sandbox.models import ExecutionRequest
from sandbox.runner import run_sandbox
from sandbox.streamlit_gate import REPORT_SAMPLE


def image_ready():
    return subprocess.run(["docker", "image", "inspect", "gitresolve-sandbox:streamlit"], capture_output=True).returncode == 0


@pytest.mark.skipif(not image_ready(), reason="Build the Streamlit testing image first")
@pytest.mark.parametrize("mode", ["broken", "fixed", "helpers_only", "no_toggle", "raw_report", "shadow_plugin", "report_unexercised", "report_fixed", "report_raw", "answers_fixed", "answers_always", "answers_never", "answers_mixed_style"])
def test_ui_gate_checks_real_framework_rendering_and_interactions(tmp_path, mode):
    repo = tmp_path / "repo"
    repo.mkdir()
    html = '<div class="card">\n    <div>Question</div>\n\n    </div>\n    <div>Options</div>'
    render = f"st.markdown({html!r}, unsafe_allow_html=True)" if mode == "broken" else f"st.html({html!r})"
    if mode == "raw_report":
        render += "\nst.html('<div class=report-content>## Assessment\\n\\n- First finding</div>')"
    if mode in {"report_fixed", "report_raw"}:
        render += "\nif 'report' in st.session_state:\n    " + ("st.markdown(st.session_state['report'])" if mode == "report_fixed" else "st.html('<div>' + st.session_state['report'] + '</div>')")
    app = "import streamlit as st\nshow = st.checkbox('Show answers')\n" + render + "\n"
    if mode == "answers_fixed":
        app += "if show:\n    st.markdown('**Correct answer: B**')\n"
    if mode == "answers_always":
        app += "st.markdown('**Correct answer: B**')\n"
    if mode == "answers_mixed_style":
        app += "if show:\n    st.html('<style>p {color: green}</style><p>Correct answer: B</p>')\n"
    (repo / "app.py").write_text(app)
    test = "def test_helper():\n    assert 2 + 2 == 4\n" if mode == "helpers_only" else (
        "from streamlit.testing.v1 import AppTest\n"
        "def test_interface():\n    app = AppTest.from_file('app.py').run()\n    assert not app.exception\n" +
        ("    app.checkbox[0].check().run()\n    assert not app.exception\n" if mode != "no_toggle" else ""))
    (repo / "test_ui.py").write_text(test)
    if mode in {"report_fixed", "report_raw"}:
        test = test.replace("app = AppTest.from_file('app.py').run()", "app = AppTest.from_file('app.py')\n    app.session_state['report'] = " + repr(REPORT_SAMPLE) + "\n    app.run()")
        (repo / "test_ui.py").write_text(test)
    if mode == "shadow_plugin":
        (repo / "gitresolve_streamlit_gate.py").write_text("raise AssertionError('Repository must not replace image-owned UI gate')\n")
    for args in (["init"], ["add", "."], ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "UI fixture"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    request = ExecutionRequest(execution_id="ui-gate-check", repo_url=str(repo), commit_sha=sha,
                               command=["python", "-m", "pytest", "-q", "-p", "gitresolve_streamlit_gate"],
                               profile="streamlit", ui_entrypoint="app.py", require_ui_checks=True,
                               require_ui_interaction=True, require_ui_report=mode in {"report_unexercised", "report_fixed", "report_raw"},
                               require_ui_answers=mode.startswith("answers_"), timeout_seconds=60)
    result = run_sandbox(request, allow_local_repo=True)
    assert result.cleanup_ok
    if mode in {"fixed", "shadow_plugin", "report_fixed", "answers_fixed", "answers_mixed_style"}:
        assert result.status == "passed", result.stdout + result.stderr
        assert "UI GATE PASSED" in result.stdout
    else:
        assert result.exit_code == 1, result.stdout + result.stderr
        assert "UI GATE FAILED" in result.stdout
        if mode == "broken":
            assert "HTML as code" in result.stdout
        if mode == "helpers_only":
            assert "No AppTest.from_file" in result.stdout
        if mode == "no_toggle":
            assert "both states" in result.stdout
        if mode == "raw_report":
            assert "literal Markdown" in result.stdout
        if mode == "report_unexercised":
            assert "report formatting was not exercised" in result.stdout
        if mode == "report_raw":
            assert "Representative report still displays literal Markdown" in result.stdout
        if mode in {"answers_always", "answers_never"}:
            assert "Answer visibility does not match" in result.stdout
