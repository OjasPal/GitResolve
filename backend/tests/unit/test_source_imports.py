from sandbox.streamlit_gate import missing_local_imports


def test_missing_real_export_cannot_be_hidden_by_test_mocks(tmp_path):
    (tmp_path / "utils.py").write_text("def parse_quiz(): return []\n")
    entry = tmp_path / "app.py"
    entry.write_text("from utils import parse_quiz, generate_mcqs_docx\n")
    errors = missing_local_imports(entry, tmp_path)
    assert len(errors) == 1 and "generate_mcqs_docx" in errors[0]
    (tmp_path / "utils.py").write_text("def parse_quiz(): return []\ndef generate_mcqs_docx(): return b'docx'\n")
    assert missing_local_imports(entry, tmp_path) == []


def test_local_reexports_and_external_dependencies_are_allowed(tmp_path):
    (tmp_path / "helpers.py").write_text("def export(): return b'docx'\n")
    (tmp_path / "utils.py").write_text("from helpers import export as generate_mcqs_docx\n")
    entry = tmp_path / "app.py"
    entry.write_text("from utils import generate_mcqs_docx\nfrom streamlit import download_button\n")
    assert missing_local_imports(entry, tmp_path) == []
