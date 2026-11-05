"""Run immutable original tests against /workspace, even after their deletion."""
import os
from pathlib import Path
import sys
import types
import pytest


class SavedModule(pytest.Module):
    def _getobj(self):
        module = types.ModuleType("gitresolve_saved_" + self.path.stem)
        module.__file__ = str(Path("/workspace") / self.path.name)
        module.__package__ = ""
        sys.modules[module.__name__] = module
        exec(compile(self.path.read_bytes(), module.__file__, "exec"), module.__dict__)
        return module


class OriginalTests:
    def pytest_pycollect_makemodule(self, module_path, parent):
        if module_path.parent == Path("/input"):
            return SavedModule.from_parent(parent, path=module_path)


if __name__ == "__main__":
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    if os.environ.get("GITRESOLVE_STREAMLIT_ENTRY"):
        from gitresolve_streamlit_gate import with_sandbox_timeout
        from streamlit.testing.v1 import AppTest

        AppTest._run = with_sandbox_timeout(AppTest._run)
    sys.path.insert(0,"/workspace")
    raise SystemExit(pytest.main(sys.argv[1:],plugins=[OriginalTests()]))
