"""Framework adapters selected from static repository evidence."""
import ast
import re
from pathlib import PurePosixPath
from dataclasses import dataclass

def streamlit_entrypoint(originals, report):
    if not re.search(
            r"\b(?:ui|theme|layout|render|rendering|checkbox|toggle|html|css|interface|design|streamlit|appearance|formatting|glassmorphism|button|download|export|sidebar|widget)\b",
            report or "", re.I):
        report = "streamlit"
    for name in sorted(originals, key=lambda p: (p.count("/"), p)):
        if not name.endswith(".py") or PurePosixPath(name).name.startswith("test_"):
            continue
        try:
            tree = ast.parse(originals[name])
        except SyntaxError:
            continue
        if any((isinstance(n, ast.Import) and any(a.name == "streamlit" for a in n.names))
               or (isinstance(n, ast.ImportFrom) and n.module == "streamlit") for n in tree.body):
            return name
    return ""


@dataclass(frozen=True)
class FrameworkAdapter:
    name: str

    def detect(self, profile):
        return profile.framework == self.name

    def env_needs(self, profile):
        return profile.env_names

    def verification_command(self, command):
        return command + (["-p", "gitresolve_streamlit_gate"] if self.name == "streamlit" else [])

    def configure(self, ctx, entrypoint, report, command):
        from app.schemas.enums import EventType
        if entrypoint:
            ctx.sandbox_profile = "streamlit"
            ctx.ui_entrypoint = entrypoint
            ctx.require_ui_checks = False
            ctx.require_ui_interaction = bool(re.search(r"checkbox|toggle|show answers", report, re.I))
            ctx.require_ui_report = bool(re.search(r"\b(?:report|evaluation|assessment)\b", report, re.I))
            ctx.require_ui_answers = ctx.require_ui_interaction and bool(re.search(r"\banswers?\b", report, re.I))
            command = self.verification_command(command)
            ctx.event(EventType.INFO, f"Streamlit UI repair: actual AppTest execution of {entrypoint} and Markdown rendering checks are required before approval.")
        return command

    def review_rules(self):
        return "For Streamlit ensure real AppTest runs/interactions and no model re-invocation on toggle." if self.name == "streamlit" else ""

    def extra_prompt_rules(self, entrypoint, ctx):
        if self.name != "streamlit":
            return "Use the declared repository dependencies and verify behavior with pytest."
        return f"""For Streamlit download exports, download_button.value is a boolean, never the download bytes. Test the export helper's returned bytes independently, and use AppTest only to verify that the button renders.
The sandbox profile is {'Streamlit 1.66, pandas, PyPDF2, dotenv, langchain-core, markdown-it-py and pytest' if entrypoint else 'Python and pytest'}.
{('MANDATORY: Add AppTest.from_file(' + repr(entrypoint) + ') tests against the REAL app. Seed representative generated data in session_state and exercise requested checkbox both unchecked and checked. Mock ONLY external model/network boundaries (e.g. the module exporting the generation chain), never Streamlit, widgets, render helpers, or the app. Assert no app exceptions, preserved quiz data and no model calls on toggle. The image-owned gate asserts correct Markdown rendering during AppTest execution. Pure helper/AST/string-presence tests alone WILL FAIL. Repository tests must be portable: do not import GitResolve-only testing modules or add undeclared test dependencies.') if entrypoint else ''}
{('For requested report formatting, set the ACTUAL report session-state field before app.run() to this literal Markdown sample: "## Evaluation report\\n\\n**Report formatting regression sample**\\n\\n- First finding\\n- Second finding\\n\\nA readable assessment paragraph." The report must render headings/bold/lists; omitting the sample cannot pass the UI gate.') if entrypoint and ctx.require_ui_report else ''}
For Streamlit UI requests, style the actual .stApp/app view container, not only the body. Preserve generated state across widget reruns.
Use native Streamlit widgets/layouts for interaction and st.html for HTML blocks; indented multiline HTML in st.markdown may become literal code.
Render evaluation reports as readable headings/paragraphs/lists, not an escaped raw blob of Markdown. Preserve existing behavior and avoid broad rewrites."""


def adapter_for(profile):
    # Flask/FastAPI/Django intentionally use generic pytest verification for now.
    return FrameworkAdapter(profile.framework)
