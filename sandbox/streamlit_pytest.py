"""Load the image-owned UI gate before adding repository code to the import path."""
import importlib.util
import os
from pathlib import Path
import sys

import pytest
import markdown_it
import streamlit.testing.v1

os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
name = "gitresolve_streamlit_gate"
spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("gitresolve_streamlit_gate.py"))
gate = importlib.util.module_from_spec(spec)
sys.modules[name] = gate
spec.loader.exec_module(gate)
sys.path.insert(0, "/workspace")
args = sys.argv[1:]
for index in range(len(args) - 2, -1, -1):
    if args[index:index + 2] == ["-p", name]:
        del args[index:index + 2]
raise SystemExit(pytest.main(args, plugins=[gate]))
