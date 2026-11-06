"""Safe fixture-only timing: fixture code executes exclusively in hardened Docker."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sandbox.models import ExecutionRequest
from sandbox.runner import run_sandbox
from githubapp.timing import capture_timings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    fixtures = Path(__file__).resolve().parents[1] / 'backend/tests/fixtures/python_repos'
    rows = []
    for fixture in ('stdlib', 'requirements', 'streamlit'):
        for iteration in range(2):
            def source(request, workspace, deadline, cancel):
                return Path(shutil.copytree(fixtures / fixture, workspace / 'repo'))
            request = ExecutionRequest(execution_id='speed-' + fixture,
                repo_url='https://github.com/fixtures/python', commit_sha='a' * 40,
                command=['python', '-m', 'pytest', '-q', '--tb=short'],
                prepare_dependencies=True, timeout_seconds=300,
                profile='streamlit' if fixture == 'streamlit' else 'python',
                ui_entrypoint='app.py' if fixture == 'streamlit' else '')
            with capture_timings() as spans:
                result = run_sandbox(request, source_provider=source)
            rows.append({'fixture': fixture, 'pass': iteration + 1,
                         'duration_ms': result.duration_ms, 'status': result.status,
                         'cleanup_ok': result.cleanup_ok, 'spans': spans,
                         'model_calls': 0, 'input_tokens': 0, 'output_tokens': 0})
            Path(args.output).write_text(json.dumps(rows, indent=2))
            print(fixture, iteration + 1, result.duration_ms, result.status, flush=True)
            if result.status != 'passed' or not result.cleanup_ok:
                raise SystemExit('Fixture failed; inspect sandbox availability before continuing.')


if __name__ == '__main__':
    main()
