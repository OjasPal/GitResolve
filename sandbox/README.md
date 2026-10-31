# Sandbox execution contract

Build: `docker build -t gitresolve-sandbox:poc sandbox`.
Test: `python -m pytest sandbox -q -ra`.

The worker calls `run_sandbox(ExecutionRequest(...), cancel=callback)`.
The callback returns true for cancellation, task deadline or lost lease.
Never invoke repository commands directly in the API/worker process.
Execution IDs correlate results with a task/attempt. Supply an exact 40-character
commit SHA and an argv command, never host shell text. Results distinguish
process exit failure, timeout, infrastructure error and OOM evidence.
Cancelled executions return error_code=cancelled. No test counts are invented:
exit code zero alone is not sufficient evidence for approving a patch.

Container writes use a 1 GiB tmpfs owned by UID 10001; input is mounted read-only
and copied without Git metadata. The 256 MiB temporary directory and workspace
both count against the 2 GiB memory limit. Network is disabled during execution.

Current limitations: host checkout is checked for size after cloning; fetch-time
disk quota is not yet enforced. Run on a dedicated quota-limited volume and only
trusted public repositories until acquisition is moved into a bounded container.
Docker log collection is buffered (rotation caps logs at 20 MiB) before response
truncation. Cancellation during checkout waits for the bounded Git call.
Patch application, dependency installation, authenticated GitHub App access,
and retention of approved diffs are not implemented by this runner.

Docker tests must pass on Linux before treating isolation as validated.
