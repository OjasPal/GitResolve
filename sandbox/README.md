# Sandbox execution contract

Build: `docker build -t gitresolve-sandbox:poc sandbox`.
Test: `python -m pytest sandbox -q -ra`.
Docker tests require a Linux Docker engine and the freshly built image.

The worker calls `run_sandbox(ExecutionRequest(...), cancel=callback)`.
Provide the task ID, public GitHub HTTPS URL, exact commit SHA, argv command,
optional unified patch, timeout (1-300 seconds), and output cap. Cancellation
returns error_code=cancelled, deadline returns timed_out, and OOM/preparation/
cleanup errors are distinct. Commands and patch application run only in Docker.

The deadline includes acquisition, preparation, execution and log retrieval.
Cleanup has a separate 10-second Docker-call limit. HTTP reads have a 5-second
inactivity timeout, so download cancellation can take up to 5 seconds. Docker
subprocesses are polled every 50ms. cleanup_ok=false requires operator action;
the container name appears in the error. Do not retry blindly after cleanup fails.

Acquisition uses public GitHub commit archives without credentials, redirects,
proxy environment or host checkout. Download is capped at 32 MiB, expanded files
at 256 MiB, entries at 10,000, and decompressed data plus metadata at 264 MiB.
Extraction rejects links, devices, sparse files, traversal, duplicate paths,
Git metadata and Windows reserved paths. Host storage per run is bounded by the
archive and expanded-file caps. Use a dedicated worker with bounded concurrency
and disk capacity; this is not a global quota across worker processes.
Local Git archives are supported only through the explicit test-only flag.

Input and patch are read-only mounts. Workspace writes use a 1 GiB tmpfs owned
by UID/GID 10001; /tmp is a 256 MiB tmpfs. Both count against the 2 GiB memory/swap
cap. Root filesystem is read-only, network disabled, all capabilities dropped,
privilege escalation disabled, CPUs capped at 2 and PIDs at 256. No Docker socket
or host credentials enter the container. The worker's Docker daemon access is
privileged: use a dedicated execution host separate from the API. Docker is not
a hardened multi-tenant VM boundary.

Patch application rejects protected paths, binary/mode/rename/delete operations,
more than 5 files or 150 changed lines, and files over 100 KB. git apply --check
and application run inside the container before the command. Dependency install
and network package resolution are not performed; the image includes Python and
pytest. Projects needing other dependencies need a reviewed image.

Docker logs rotate at 1 MiB. Returned stdout+stderr share a bounded capture cap.
stdout_truncated/stderr_truncated describe capture limits; logs_complete=false
warns that earlier rotation may have discarded output. Sanitized logs remain
untrusted repository data. Exit zero does not prove reproduction, test counts or
regression coverage. The agent must supply actual verification evidence.

Private repository authentication, GitHub App integration, patch generation,
commits and PR creation are not implemented by this runner. Live Docker tests
must pass on Linux before treating the isolation controls as validated.
