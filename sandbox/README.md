# Sandbox execution contract

Build: `docker build -t gitresolve-sandbox:poc sandbox`.
Test: `python -m pytest backend/tests/sandbox -q -ra`.
Docker tests require a Linux Docker engine and the freshly built image.
Execution never pulls a missing image automatically; build the reviewed image first.

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
and application run inside the container before the command. The generic image contains Python 3.12 and pytest. Declared Python dependencies
use the wheel-only preparation flow below; repository build scripts never run.

The API has one narrow deletion exception: a human-approved cleanup manifest for
one to four root `test*.py` files at a pinned revision. Only the trusted worker
supplies `allowed_deletions`; model output and repository contents cannot grant
this permission. Cleanup allows complete-file removals only, with the exact
task-scoped line budget approved in the plan (ceiling 400). The image-owned parent
checks removed paths, local references and unchanged application source, and
reruns immutable original tests against `/workspace` in a separate pytest child.
The passing cleanup marker, test evidence and exact diff hash are required for
publication. Default repair requests still reject deletion.

Docker logs rotate at 20 MiB. Returned stdout+stderr share a bounded capture cap.
stdout_truncated/stderr_truncated describe capture limits; logs_complete=false
warns that earlier rotation may have discarded output. Sanitized logs remain
untrusted repository data. Exit zero does not prove reproduction, test counts or
regression coverage. The agent must supply actual verification evidence.

Private repository authentication, GitHub App integration, patch generation,
commits and PR creation are not implemented by this runner. Live Docker tests
must pass on Linux before treating the isolation controls as validated.

## Streamlit verification profile

Build `gitresolve-sandbox:streamlit` with `sandbox/streamlit.Dockerfile` after
building the base image. It adds Streamlit 1.66, markdown-it-py, PyPDF2, dotenv,
and langchain-core through trusted image construction, never runtime installation.
The runner selects only the `python` or `streamlit` image; requests cannot choose
arbitrary images. Resource limits and isolation are identical for both profiles.

For Streamlit, an image-owned pytest launcher preloads the framework and quality
gate before repository imports. Final verification requires AppTest execution of
the actual detected entry point, requested checkbox transitions, and no HTML-as-code
or unformatted report Markdown in the emitted UI. Missing evidence blocks success.
This is framework/rendering verification, not a pixel-perfect browser assessment.


## Static profiling and wheel-only environments

The repair worker profiles the pinned snapshot before its first model repair call.
Inspection parses Python AST and TOML only: language/version, framework, dependency
and lock files, entrypoints, tests, and environment-variable names. Non-Python
repositories are explicitly unsupported. Flask/FastAPI/Django currently use generic
pytest adapters; Streamlit retains its image-owned AppTest gates and prompt rules.
Context consists of a compact file tree and numbered excerpts ranked by issue
paths/identifiers, entrypoints and configuration, under a strict character budget.

Set `ExecutionRequest.prepare_dependencies=True` (the repair worker does this)
to prepare declared `requirements.txt`, test requirements, or PEP 621 project
requirements. Relative `-r` includes must remain inside the repository. VCS, URLs,
local paths, editable installs, custom indexes, unsafe includes and dynamic
metadata are rejected. Other lock formats are inventoried and contribute to the
cache key; they are not silently treated as authoritative dependency solvers.
Legacy setup/Poetry/Pipfile-only projects need a static requirements export.
The supported target is CPython 3.12 on Linux x86-64 with manylinux2014 wheels.
An incompatible Python requirement or unavailable binary wheel produces a user
action report, not an attempt to build an sdist.

Preparation has two phases:

1. A trusted host Python interpreter invokes isolated pip download with
   `--only-binary=:all: --no-deps`, a fixed public PyPI index, and explicit target
   tags. No repository paths/options are passed to pip. Credentials, proxy
   settings, pip config, netrc and keyring lookup are disabled. Wheel ZIP metadata
   is inspected before each transitive requirement is submitted; direct URLs in
   metadata are rejected. Resolution is bounded to 12 rounds and 250 requirements.
   Wheels are never imported or installed on the host. The completed wheel cache
   key hashes dependency-file contents, Python version and reviewed base image ID;
   atomic publication and SHA-256 manifests prevent incomplete cache reuse.
2. A disposable container, with network disabled and resource limits, creates a
   virtual environment in a Docker volume and installs wheels using `--no-index`,
   `--no-deps`, and `--find-links=/wheelhouse`. It sees only the wheelhouse and its
   output volume, not repository code or host secrets. The test container mounts
   this environment read-only at `/opt/prepared`, retains the existing network,
   user, capability, filesystem and resource restrictions, and uses its Python.
   The preparation container is always removed. Successful volumes are leased
   from a bounded process-local cache (8 entries, 30-minute TTL); executions mount
   them read-only. Uncached/failed volumes are removed, idle expired volumes are
   reclaimed on preparation, and normal process shutdown removes idle volumes.
   Abrupt host/process termination can leave `gitresolve-env-*` volumes for an
   operator to remove when no worker uses them. Runtime containers are never reused.

The venv inherits the reviewed image's pytest/framework tooling. Declared wheels
can augment that image but never authorize undeclared imports or bypass policy.
Cached wheels live under the host temporary directory in
`gitresolve-wheel-cache-v1`; HTTP wheel-download caching is separate. Cache entries
are not uploaded. Administrators may remove unused entries when no job uses them.

Baseline outcomes are deterministic: missing_dep, missing_secret,
invalid_dependency_spec, preexisting_test_failure, collection_error,
sandbox_prep_failure, timeout_resource (plus passed/no_tests). Missing packages
and secrets are never reproduction evidence. A broken imported local symbol is a
potential preexisting code failure; other collection failures require clarification.
Missing secrets are reported with steps to mock external services or provide safe
test fixtures, never a request to paste a key. Infrastructure outcomes stop repair
without charging a repair attempt; model-call usage already incurred stays counted.
The existing bounded infra retry and exact-diff approval gates remain in force.

## Speed caches and speculation

The baseline cache is process-local, at most 32 entries, five-minute TTL. Only
successful original pytest baselines with nontruncated evidence and clean cleanup
qualify. Keys include user/installation scope, repository URL, pinned SHA,
dependency hash, actual leased environment identity, reviewed image ID, command,
verifier version and framework/check options. Environment integrity is checked
before lookup. A rebuild/eviction changes the environment identity. Patched
requests are never eligible. Cache hits are explicitly marked and cannot replace
the new regression's original failure or the patched final suite.

Plan-review prewarming is separately bounded to one job per process and 60 seconds,
with no waiting queue. It stops on rejection, cancellation, base or gate revision
changes, never changes task state, and uses the same sandbox/cache path without
credentials or persistent runtime containers. It performs no edits or model calls.
Runtime stage telemetry separates speculation from the active worker phase.

Real integration command:

```powershell
$env:ENVIRONMENT='test'
.venv/Scripts/python.exe -m pytest backend/tests/sandbox/test_prepared_environments.py -q -ra
```

The test executes stdlib, requirements.txt, pyproject, Flask and Streamlit fixtures,
plus missing-dependency, missing-secret and collection-error cases. Prepared cases
assert their venv path, read-only mount and disabled network. Missing Docker or
images produces an explicit REAL DOCKER INTEGRATION SKIPPED reason. Unit tests do
not count as integration evidence. Streamlit gate regression tests remain in
`backend/tests/sandbox/test_streamlit_gate.py`.
