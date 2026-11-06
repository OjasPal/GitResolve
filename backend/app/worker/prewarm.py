"""One speculative baseline at a time, only during an authorized task's plan review."""
from contextvars import Context
from pathlib import Path
from types import SimpleNamespace
import logging
import shutil
import tempfile
import threading
import time

_slot = threading.BoundedSemaphore(1)


def start(service, github, task_id):
    if github is None or not _slot.acquire(blocking=False):
        return False

    def work():
        from sandbox.models import ExecutionRequest
        from sandbox.runner import run_sandbox
        from sandbox.profiler import profile_repository
        from app.agent.frameworks import adapter_for, streamlit_entrypoint
        from githubapp.timing import capture_timings, measure
        try:
            task = service.store.get(f'tasks/{task_id}') or {}
            if task.get('status') != 'awaiting_start' or not task.get('baseSha') or task.get('cleanupScope'):
                return
            deadline = time.monotonic() + 60
            def cancel():
                current = service.store.get(f'tasks/{task_id}') or {}
                return (time.monotonic() >= deadline or current.get('cancelRequested')
                        or current.get('baseSha') != task['baseSha']
                        or current.get('gateRevision', 0) != task.get('gateRevision', 0)
                        or current.get('status') not in {'awaiting_start', 'queued', 'running'})
            with capture_timings(), measure('prewarm.baseline', task_id=task_id):
                with tempfile.TemporaryDirectory(prefix='gitresolve-prewarm-') as directory:
                    request = SimpleNamespace(repo_url=f"https://github.com/{task['repoFullName']}", commit_sha=task['baseSha'])
                    root = github.source(task)(request, Path(directory), deadline, cancel)
                    profile = profile_repository(root)
                    if profile.language == 'unsupported' or cancel():
                        return
                    originals = {p.relative_to(root).as_posix(): p.read_text(encoding='utf-8', errors='replace')
                                 for p in root.rglob('*.py') if not p.is_symlink() and p.stat().st_size <= 100000}
                    report = task['issue'].get('body') or task['issue'].get('title') or ''
                    entry = streamlit_entrypoint(originals, report) if profile.framework == 'streamlit' else ''
                    config = SimpleNamespace(event=lambda *args: None)
                    command = adapter_for(profile).configure(config, entry, report,
                        ['python', '-m', 'pytest', '-q', '--tb=short'])
                    remaining = int(deadline - time.monotonic())
                    if remaining < 1:
                        return
                    execution = ExecutionRequest(execution_id='prewarm', repo_url=request.repo_url,
                        commit_sha=request.commit_sha, command=command, timeout_seconds=remaining,
                        prepare_dependencies=True, profile=getattr(config, 'sandbox_profile', 'python'),
                        ui_entrypoint=entry, require_ui_interaction=getattr(config, 'require_ui_interaction', False),
                        require_ui_report=getattr(config, 'require_ui_report', False),
                        require_ui_answers=getattr(config, 'require_ui_answers', False))
                    def source(request, workspace, deadline, cancel):
                        # Snapshot extraction already normalizes metadata. Preserve
                        # permissions without redundant timestamp/xattr copies.
                        return Path(shutil.copytree(root, workspace / 'repo', copy_function=shutil.copy))
                    run_sandbox(execution, cancel=cancel, source_provider=source,
                                baseline_scope=f"{task['userId']}:{task['repoId']}")
        except Exception:
            # Speculation never changes task status or converts errors into evidence.
            logging.getLogger('gitresolve.performance').info('prewarm unavailable', extra={'task_id': task_id})
        finally:
            _slot.release()

    # Independent timing scope: speculation must not mutate a finished worker phase.
    threading.Thread(target=Context().run, args=(work,), daemon=True, name='baseline-prewarm').start()
    return True
