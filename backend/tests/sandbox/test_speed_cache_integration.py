"""Real Docker assertions: reuse original evidence only, never patched verification."""
from pathlib import Path
import shutil
from sandbox.models import ExecutionRequest
from sandbox.runner import run_sandbox
from sandbox.baseline_cache import CACHE
from githubapp.timing import capture_timings
from tests.sandbox.test_runner import needs_docker


@needs_docker
def test_cached_baseline_never_replaces_patched_container(tmp_path):
    repo = tmp_path / 'source'; repo.mkdir()
    (repo / 'calc.py').write_text('value = 1\n', newline='\n')
    (repo / 'test_calc.py').write_text('from calc import value\ndef test_value():\n    assert value >= 1\n', newline='\n')
    def source(request, workspace, deadline, cancel):
        return Path(shutil.copytree(repo, workspace / 'repo'))
    request = ExecutionRequest(execution_id='cache-integration', repo_url='https://github.com/fixtures/cache',
        commit_sha='e'*40, command=['python', '-m', 'pytest', '-q'], timeout_seconds=60, prepare_dependencies=True)
    CACHE.clear()
    try:
        with capture_timings() as cold:
            original = run_sandbox(request, source_provider=source, baseline_scope='alice:10')
        assert original.status == 'passed' and not original.baseline_cache_hit
        assert sum(r['operation'] == 'docker.run' for r in cold) == 1
        with capture_timings() as warm:
            repeated = run_sandbox(request, source_provider=source, baseline_scope='alice:10')
        assert repeated.status == 'passed' and repeated.baseline_cache_hit and repeated.cleanup_ok
        assert not any(r['operation'] == 'docker.run' for r in warm)
        patched = request.model_copy(update={'patch': '--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n'})
        with capture_timings() as verification:
            verified = run_sandbox(patched, source_provider=source, baseline_scope='alice:10')
        assert verified.status == 'passed' and not verified.baseline_cache_hit
        assert sum(r['operation'] == 'docker.run' for r in verification) == 1
        with capture_timings() as isolated:
            other_user = run_sandbox(request, source_provider=source, baseline_scope='bob:10')
        assert other_user.status == 'passed' and not other_user.baseline_cache_hit
        assert sum(r['operation'] == 'docker.run' for r in isolated) == 1
        stopped = run_sandbox(request, source_provider=source, baseline_scope='alice:10', cancel=lambda: True)
        assert stopped.status != 'passed'
    finally:
        CACHE.clear()
