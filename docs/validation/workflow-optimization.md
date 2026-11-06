# Workflow reliability and public outcomes

All work is local on `agent/workflow-optimization`; no push or merge.

## Error boundary

`core.errors.public_error` maps exceptions to a category, safe message, and incident ID. The original traceback is logged server-side; credentials remain redacted in structured logs. Host paths are stripped from public text, while repository-relative paths and assertion diagnostics remain usable. Existing `sanitize_error` delegates to this boundary.

Worker-service events, artifacts, failures, transition payloads, and public task response models sanitize output. Response sanitization also covers older stored task data. Authentication token responses intentionally retain their token. Final diffs are never silently rewritten: unsafe content is rejected at storage, review, approval, and publication boundaries.

## Outcomes

The task detail API adds `outcome` and `userMessage`. An outcome contains `kind`, `reasonCode`, `explanation`, `evidence`, and `nextStep`. Evidence defaults to available artifact identifiers. Existing status and failure-code values remain unchanged.

| Outcome | Existing status |
| --- | --- |
| fix_ready | awaiting_approval |
| needs_user_action | failed_infra (access_revoked) |
| needs_clarification | needs_input |
| cannot_reproduce | failed_not_reproduced |
| out_of_scope | failed_policy |
| cannot_fix_with_report | failure-code-specific terminal status |
| infra_retry | queued for planning; running for execution |

Infrastructure retries retain the active-task slot, deadline, attempt counts, and budgets, release the old lease, and allow at most two automatic requeues per task. Exhaustion produces a terminal report, never a terminal `infra_retry`. Policy, access, budget and reproduction failures cannot request infrastructure retries. PR publication is not automatically retried; its existing human retry/approval path remains in force.

Frontend follow-up only (not implemented): add the two API fields to generated types, display `userMessage` with the outcome next step and evidence links, and label `infra_retry` as retrying rather than failed. No redesign is needed.

## Local persistence and leases

Atomic state-file replacement retries transient PermissionError five times with 25/50/100/200 ms delays. A failed write never publishes staged memory changes. Temporary-file deletion also retries transient access denial. If the OS continues denying deletion, the operation reports a safe infrastructure error; no implementation can guarantee deleting a file that remains locked by another process.

A held OS file lock beside the state file prevents a second store owner. Use the embedded API worker for local persistence, or Firestore for separate API and worker processes. Lock ownership is released on close/process exit, and closed or fork-inherited owners cannot persist changes.

Frequent checkpoints validate the durable lease every time, but renew it at most once per `min(heartbeat_seconds, lease_seconds / 3)`. A skipped renewal does not advance memory-only expiry. Heartbeat failure counts, durable expiry, last heartbeat, and task ID appear in structured worker logs. Unknown or expired leases stop further work. Docker cancellation polls checkpoints; an already in-flight blocking model request may finish, but cannot proceed to another operation or publish its result after lease loss.

Failure-injection tests demonstrate that persistent heartbeat storage failure or absent heartbeats can cause `worker_lost`. They do not establish the cause of the historical Reproduce incident.

## Validation

Use `ENVIRONMENT=test` so test collection does not open the developer's local state file. On this Windows host, FastAPI's test client needs local socket access; the restricted run hung in asyncio socket-pair creation and was interrupted.

Targeted command:

```powershell
$env:ENVIRONMENT='test'; .venv/Scripts/python.exe -m pytest backend/tests/unit/test_workflow_optimization.py backend/tests/unit/test_repair_runner.py backend/tests/unit/test_policy.py backend/tests/integration/test_task_lifecycle.py -q --tb=short
```

Result: 81 passed before final review.

The full suite was run exactly once:

```powershell
$env:ENVIRONMENT='test'; .venv/Scripts/python.exe -m pytest -q --tb=short -ra
```

Result: 337 passed, 3 failed, 27 skipped in 24.29 seconds. The three failures were old assertions expecting raw provider messages or immediate terminal failure. These assertions were updated to check incident references and bounded retry exhaustion, then passed in the focused runs below. No full-suite rerun was performed.

```powershell
$env:ENVIRONMENT='test'; .venv/Scripts/python.exe -m pytest backend/tests/agent/test_graph.py backend/tests/integration/test_langgraph_adapter.py backend/tests/integration/test_model_recovery.py -q --tb=short
```

Result: 10 passed in 2.62 seconds.

```powershell
$env:ENVIRONMENT='test'; .venv/Scripts/python.exe -m pytest backend/tests/unit/test_workflow_optimization.py backend/tests/unit/test_core.py backend/tests/unit/test_policy.py backend/tests/integration/test_task_lifecycle.py -q --tb=short
```

Result: 75 passed in 4.77 seconds, including quoted credentials and legacy event data.

```powershell
$env:ENVIRONMENT='test'; .venv/Scripts/python.exe -m pytest backend/tests/unit/test_workflow_optimization.py backend/tests/unit/test_local_persistence.py backend/tests/agent/test_graph.py backend/tests/integration/test_langgraph_adapter.py backend/tests/integration/test_model_recovery.py -q --tb=short
```

Final focused result: 42 passed in 5.38 seconds.

Docker Desktop was started using the existing installation and test images. Previously skipped Docker checks were then exercised:

```powershell
$env:ENVIRONMENT='test'; .venv/Scripts/python.exe -m pytest backend/tests/sandbox backend/tests/integration/test_github_docker_flow.py -q --tb=short -ra
```

Result: 57 passed in 64.36 seconds. Tests retain Docker network isolation. Firestore emulator/dependency checks and the ClamAV-dependent test remain unverified because those services/dependencies are unavailable.

`git diff --check`: passed. Tracked source, documentation, and environment examples contain no obsolete provider references. No frontend files changed.
