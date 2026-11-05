"""Trusted pytest plugin for framework rendering/interaction checks, not visual design scoring."""
from html.parser import HTMLParser
from pathlib import Path
import os
import re
import ast


def missing_local_imports(entrypoint, root):
    """Check real source exports even when generated tests replace modules with mocks.

    This inspects source only; it never imports or executes repository code.
    Dynamic export modules are left to the framework execution checks.
    """
    errors = []
    pending = [Path(entrypoint)]
    seen = set()
    root = Path(root).resolve()
    while pending:
        source = pending.pop().resolve()
        if source in seen or not source.is_relative_to(root):
            continue
        seen.add(source)
        tree = ast.parse(source.read_text(encoding="utf-8-sig"), filename=str(source))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            base = source.parent if node.level else root
            for _ in range(max(0, node.level - 1)):
                base = base.parent
            module = base.joinpath(*node.module.split("."))
            target = module.with_suffix(".py")
            if not target.is_file():
                target = module / "__init__.py"
            if not target.is_file() or not target.resolve().is_relative_to(root):
                continue  # external dependency, checked during actual execution
            pending.append(target)
            definitions = ast.parse(target.read_text(encoding="utf-8-sig"))
            names = {n.id for n in ast.walk(definitions) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
            names.update(n.name for n in ast.walk(definitions) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
            for definition in ast.walk(definitions):
                if isinstance(definition, (ast.Import, ast.ImportFrom)):
                    names.update(a.asname or a.name.split(".")[0] for a in definition.names)
            if "__getattr__" in names or any(isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names) for n in ast.walk(definitions)):
                continue
            for alias in node.names:
                if alias.name == "*" or alias.name in names or (module / (alias.name + ".py")).is_file() or (module / alias.name / "__init__.py").is_file():
                    continue
                errors.append(f"{source.relative_to(root)} imports missing local export {node.module}.{alias.name}. Verify the real source, not a mocked replacement.")
    return errors

REPORT_PROBE = "Report formatting regression sample"
REPORT_SAMPLE = f"## Evaluation report\n\n**{REPORT_PROBE}**\n\n- First finding\n- Second finding\n\nA readable assessment paragraph."


class MarkupAsCode(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.broken = False
        self.report_depth = 0
        self.raw_report = False

    def handle_starttag(self, tag, attrs):
        if tag in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            return
        if self.report_depth:
            self.report_depth += 1
        elif tag in {"div", "section", "article"} and any(k == "class" and re.search(r"report|evaluation|review", v or "", re.I) for k, v in attrs):
            self.report_depth = 1
        if tag == "code":
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            return
        if self.report_depth:
            self.report_depth -= 1
        if tag == "code":
            self.depth = max(0, self.depth - 1)

    def handle_data(self, data):
        if self.depth and re.search(r"</?(?:div|span|section|article|p)\b", data):
            self.broken = True
        if self.report_depth and re.search(r"(?m)^\s*(?:#{1,6}\s+|[-*]\s+)|\*\*[^*\n]+\*\*", data):
            self.raw_report = True


def markdown_has_raw_markup(text):
    from markdown_it import MarkdownIt
    parsed = MarkupAsCode()
    parsed.feed(MarkdownIt("commonmark", {"html": True}).render(text))
    return parsed.broken


def unformatted_report(html):
    parsed = MarkupAsCode()
    parsed.feed(html)
    return parsed.raw_report


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"style", "script"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"style", "script"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, text):
        if not self.hidden:
            self.text.append(text)


def with_sandbox_timeout(run):
    """Allow cold container startup while respecting explicit test deadlines."""
    def run_with_timeout(app, widget_state=None, timeout=None):
        if timeout is None:
            timeout = max(app.default_timeout, 15)
        return run(app, widget_state, timeout=timeout)

    return run_with_timeout


def pytest_configure(config):
    from streamlit.testing.v1 import AppTest
    expected = (Path("/workspace") / os.environ["GITRESOLVE_STREAMLIT_ENTRY"]).resolve()
    evidence = {"runs": 0, "widgets": {}, "errors": [], "report_seen": False, "answer_states": set()}
    config._gitresolve_ui = evidence
    original = with_sandbox_timeout(AppTest._run)

    def checked_run(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if Path(self._script_path).resolve() != expected:
            return result
        evidence["runs"] += 1
        visible_blocks = []
        def check_report(rendered):
            parsed = VisibleText()
            parsed.feed(rendered)
            visible = "\n".join(parsed.text)
            visible_blocks.append(visible)
            if REPORT_PROBE in visible:
                evidence["report_seen"] = True
                if re.search(r"(?m)^\s*#{1,6}\s+|\*\*" + re.escape(REPORT_PROBE) + r"\*\*", visible):
                    evidence["errors"].append("Representative report still displays literal Markdown headings/bold text rather than formatted content.")
        if len(self.exception):
            evidence["errors"].append("The real Streamlit entry point raised an exception.")
        for node in self.markdown:
            from markdown_it import MarkdownIt
            check_report(MarkdownIt("commonmark", {"html": bool(getattr(node.proto, "allow_html", False))}).render(node.value))
            if getattr(node.proto, "allow_html", False) and markdown_has_raw_markup(node.value):
                evidence["errors"].append("Streamlit Markdown renders HTML as code. Use native components/st.html or correct HTML block whitespace.")
            if getattr(node.proto, "allow_html", False) and unformatted_report(node.value):
                evidence["errors"].append("Evaluation report displays literal Markdown markers inside HTML. Render report headings/lists with Markdown or native components.")
        for node in self.get("html"):
            check_report(getattr(node.proto, "body", ""))
            if unformatted_report(getattr(node.proto, "body", "")):
                evidence["errors"].append("Evaluation report displays literal Markdown markers inside HTML. Render report headings/lists with Markdown or native components.")
        for widget in self.checkbox:
            evidence["widgets"].setdefault(widget.label, set()).add(bool(widget.value))
        if os.environ.get("GITRESOLVE_REQUIRE_UI_ANSWERS") == "1":
            controls = [w for w in self.checkbox if re.search(r"answer|solution", w.label, re.I)]
            native = [str(node.value) for kind in ("text", "caption", "success", "info", "warning") for node in self.get(kind)]
            text = "\n".join(visible_blocks + native)
            revealed = bool(re.search(r"correct\s+answer\s*[:：]|answer\s*[:：]|solution\s*[:：]", text, re.I))
            for widget in controls:
                if bool(widget.value) == revealed:
                    evidence["answer_states"].add(bool(widget.value))
                else:
                    evidence["errors"].append("Answer visibility does not match its checkbox: hide answer labels by default and reveal them when checked.")
        if evidence["errors"]:
            raise AssertionError("Framework UI regression: " + " | ".join(dict.fromkeys(evidence["errors"])))
        return result

    AppTest._run = checked_run
    config._gitresolve_original_run = original


def pytest_unconfigure(config):
    from streamlit.testing.v1 import AppTest
    if hasattr(config, "_gitresolve_original_run"):
        AppTest._run = config._gitresolve_original_run


def pytest_sessionfinish(session, exitstatus):
    if os.environ.get("GITRESOLVE_REQUIRE_UI_CHECKS") != "1":
        return
    evidence = session.config._gitresolve_ui
    errors = list(evidence["errors"])
    try:
        errors.extend(missing_local_imports(Path("/workspace") / os.environ["GITRESOLVE_STREAMLIT_ENTRY"], Path("/workspace")))
    except (SyntaxError, UnicodeError, OSError) as exc:
        errors.append(f"Real application source could not be verified: {type(exc).__name__}.")
    if not evidence["runs"]:
        errors.append("No AppTest.from_file execution of the actual Streamlit entry point. Helper/AST/string tests are insufficient.")
    if os.environ.get("GITRESOLVE_REQUIRE_UI_INTERACTION") == "1" and not any(values == {False, True} for values in evidence["widgets"].values()):
        errors.append("Requested checkbox behavior was not exercised in both states on the actual app.")
    if os.environ.get("GITRESOLVE_REQUIRE_UI_REPORT") == "1" and not evidence["report_seen"]:
        errors.append("Requested report formatting was not exercised. Seed the actual report state with the required Markdown regression sample before running the real app.")
    if os.environ.get("GITRESOLVE_REQUIRE_UI_ANSWERS") == "1" and evidence["answer_states"] != {False, True}:
        errors.append("Actual answer visibility was not verified in both checkbox states; changing the widget value alone is insufficient.")
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if errors:
        session.exitstatus = 1
        reporter.write_line("UI GATE FAILED: " + " | ".join(dict.fromkeys(errors)))
    else:
        reporter.write_line(f"UI GATE PASSED: actual Streamlit entry point, {evidence['runs']} framework runs; Markdown render and requested interactions checked. Visual design still requires human review.")
