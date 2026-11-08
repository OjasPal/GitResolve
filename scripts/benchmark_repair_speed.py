"""Scripted repair benchmark. Zero live model calls; all fixture execution in Docker."""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]
from app.agent.repair_runner import run_repair, EditProposal, QualityReview
from sandbox.models import ExecutionRequest
from sandbox.runner import run_sandbox
from sandbox import environment
from sandbox.baseline_cache import CACHE
from githubapp.timing import capture_timings


def benchmark(name, optimized, iteration):
    with tempfile.TemporaryDirectory(prefix='speed-fixture-') as directory:
        root = Path(directory)
        if name == 'trivial':
            source = 'def words(text):\n    return re.findall(r"\\w+", text)\n'
            replacement = 'import re\n\n' + source
            test = 'from example import words\ndef test_words():\n    assert words("hello world") == ["hello", "world"]\n'
        else:
            source = 'from helper import combine\ndef total(a, b):\n    return combine(a, b)\n'
            (root / 'helper.py').write_text('def combine(a, b):\n    return a - b\n', newline='\n')
            (root / 'requirements.txt').write_text('packaging==24.2\n', newline='\n')
            replacement = source
            test = 'from example import total\ndef test_total():\n    assert total(2, 3) == 5\n'
        (root / 'example.py').write_text(source, newline='\n')
        (root / 'test_existing.py').write_text('def test_existing():\n    assert True\n', newline='\n')
        edit = {'path': 'example.py', 'old_text': source, 'new_text': replacement} if name == 'trivial' else {
            'path': 'helper.py', 'old_text': 'return a - b', 'new_text': 'return a + b'}
        proposal = EditProposal(explanation='Correct fixture result', edits=[edit], tests=[{'path': 'test_regression.py', 'content': test}])
        review = QualityReview(commit_message='fix: Correct fixture result', approved=True,
            requirements=[{'requirement': 'Correct output', 'status': 'passed', 'evidence': 'Original regression fails; patched suite passes.'}], blocking_issues=[])
        start = time.perf_counter()
        stages, first_action, executions = [], [], []
        class Context:
            def __init__(self):
                self.budgets = {'modelCalls': 0, 'inputTokens': 0, 'outputTokens': 0}
                self._svc = SimpleNamespace(store=SimpleNamespace(get=lambda path: {'budgets': self.budgets.copy()}))
                self.current, self.since = 'intake', start
            def checkpoint(self): pass
            def usage(self, attempts_used=None, **values): self.budgets.update(values)
            def stage(self, stage, **fields):
                now = time.perf_counter()
                stages.append({'stage': self.current, 'duration_ms': round((now-self.since)*1000, 3)})
                self.current, self.since = stage.value, now
            def event(self, *args, **kwargs): pass
            def artifact(self, *args): pass
            @contextmanager
            def repository(self): yield root
            def execute(self, *, commit_sha, command, patch='', baseline=False):
                request = ExecutionRequest(execution_id='speed-repair', repo_url=f'https://github.com/fixtures/{name}',
                    commit_sha=commit_sha, command=command, patch=patch, prepare_dependencies=True, timeout_seconds=120)
                def acquire(request, workspace, deadline, cancel): return Path(shutil.copytree(root, workspace / 'repo'))
                result = run_sandbox(request, source_provider=acquire, baseline_scope=f'fixture:{name}' if baseline and optimized else None)
                executions.append({'status': result.status, 'error': result.error_code,
                                   'stdout': result.stdout[-1500:], 'stderr': result.stderr[-1500:]})
                return result
            def baseline(self, **kwargs): return self.execute(**kwargs, baseline=True)
        ctx = Context()
        def route(prompt, **kwargs):
            if not first_action: first_action.append(round((time.perf_counter()-start)*1000, 3))
            return SimpleNamespace(result=SimpleNamespace(parsed=review if kwargs['schema'] is QualityReview else proposal),
                                   usage=None, selected_provider='scripted', selected_model='fixture')
        task = SimpleNamespace(task_id='speed', base_sha='a'*40, issue={'body': 'Correct fixture output'}, plan={},
                               limits={'maxRepairAttempts': 1, 'maxDiffFiles': 5, 'maxDiffLines': 150})
        with capture_timings() as spans:
            result = run_repair(task, ctx, SimpleNamespace(route_normal=route))
        stages.append({'stage': ctx.current, 'duration_ms': round((time.perf_counter()-ctx.since)*1000, 3)})
        row = {'fixture': name, 'optimized': optimized, 'pass': iteration, 'outcome': result.outcome,
               'duration_ms': round((time.perf_counter()-start)*1000, 3), 'stages': stages,
               'first_scripted_model_action_ms': first_action[0] if first_action else None,
               'scripted_model_actions': ctx.budgets['modelCalls'], 'live_model_calls': 0,
               'input_tokens': 0, 'output_tokens': 0, 'docker_runs': sum(s['operation']=='docker.run' for s in spans)}
        if result.outcome != 'fix_ready':
            print(result.model_dump() if hasattr(result, 'model_dump') else vars(result))
            print(executions)
            raise RuntimeError('Benchmark fixture failed verification.')
        return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    rows = []
    for optimized in (False, True):
        environment.clear_idle_environments(); CACHE.clear()
        environment.ENVIRONMENT_CACHE_LIMIT = 8 if optimized else 0
        for name in ('trivial', 'multi_file_behavior'):
            for iteration in (1, 2):
                row = benchmark(name, optimized, iteration)
                rows.append(row)
                Path(args.output).write_text(json.dumps(rows, indent=2))
                print(name, optimized, iteration, row['duration_ms'], row['docker_runs'], flush=True)


if __name__ == '__main__': main()
