# Backend foundation

Use Python 3.12. From backend: `uv pip install -e '.[dev,agent,firestore]'`.

## Frontend-free local prototype

Set ENVIRONMENT=local, STORE_BACKEND=memory, EMBEDDED_WORKER=true,
AGENT_MODE=langgraph, GEMINI_API_KEY, then run
`uvicorn app.main:app --host 127.0.0.1 --port 8000`.
The local-only embedded thread shares the memory store. Separate API/worker
processes require STORE_BACKEND=firestore and configured ADC or an emulator.

1. POST `/api/v1/auth/dev-token` with `{"userId":"1","githubLogin":"local"}`.
2. Use the returned accessToken as a Bearer token.
3. POST `/api/v1/tasks` with `{"repoId":"1001_2002","issueNumber":1}`.
4. Poll GET `/api/v1/tasks/{id}`, `/events`, and `/artifacts`.
5. Planning ends at awaiting_start. After plan approval, POST `/start` runs bounded
   patch generation, regression reproduction, and Docker tests. Verified fixes
   pause at awaiting_approval; an exact-diff approval is required before a draft PR.

GITHUB_MODE=fixture uses the local repository/issue fixture. For real GitHub
App configuration, see [GitHub integration](../docs/GITHUB_INTEGRATION.md).
AGENT_MODE=fake is explicitly a lifecycle test fixture; it is not a real repair.
GITHUB_MODE=real constructs the App repository and OAuth providers; it disables
dev-token sign-in and requires a strong session secret.

## Images and validation

API: `docker build -f backend/Dockerfile .`.
Worker: `docker build -f backend/Worker.Dockerfile .` from repository root.
The worker image includes the agent but does not provide a Docker daemon.
Use the host worker for sandbox execution until Docker transport is configured.
Never mount the Docker socket in a repository execution container.

Run `python -m pytest -q -ra` here; set FIRESTORE_EMULATOR_HOST to include
Firestore integration tests. No production deployment is implied by unit tests.

Run `python -m app.demo --mock-llm` for an asynchronous API/real-graph smoke
demonstration without provider costs. Omit the flag to use Gemini. The mock
flag replaces only the provider call, not the graph or worker.

Agent nodes may use `ctx.execute(commit_sha=..., command=[...], patch=...)`
for sandbox execution. The adapter binds the repository to
the task, caps execution by the remaining budget, and propagates cancellation
and lost leases. It does not generate patches or interpret process success as
a correct fix. Return ExecuteResult.verification with reproduced,
regressionPassed, testsRun and newFailures; finalization also requires a pinned
baseSha and a bounded diff containing a regression test. These are trusted
agent evidence fields, not model confidence flags.

Internal dailyQuota counters on users are transactional and are not exposed
in API responses. Existing user documents bootstrap from their task history.

Usage counters are cumulative and monotonic. Agent code must checkpoint between
operations, reserve/report model calls, and pass remaining time/cancellation to
execution tools. The Gemini client has a 60-second request timeout. Python
threads cannot forcibly terminate arbitrary agent code; only bounded tools are
supported. Expired tasks cannot publish results through the normal worker path.


## Local worker and progress

Settings always load `backend/.env`, regardless of the shell's working directory.
With local real GitHub mode and the memory store, the API starts its worker in
process so tasks share the same store. Set `AGENT_MODE=langgraph`. The agent's
model settings load from the project-root `.env`; process environment wins.
Install the backend `agent` extra, which includes Anthropic Vertex support.
`/health` reports API liveness plus local worker state and configured agent mode.
A healthy API alone does not imply a running worker. Separate worker processes
require Firestore, since memory is not shared. For a single local API process,
set `LOCAL_STATE_PATH` to an absolute path inside the ignored `.diagnostics/`
directory to retain tasks, connection records, events and artifacts across reloads.
Snapshots are replaced atomically; a failed write does not commit in memory.
Do not share this file between processes. Production still requires Firestore.

The repair runner reads bounded repository text without executing it on the host.
It requests compact source edits and compiles regression-only and complete Git
diffs locally, including new-file metadata and missing-final-newline markers. It validates scope/protected paths,
requires identical regression sections, reproduces a failing assertion in Docker,
and requires the patched suite to pass before offering approval. Progress/events,
attempt patches and test output are stored throughout. Tests do not prove visual
quality; UI changes still need human review. Docker is network-isolated and the
base image contains Python/pytest only. Projects requiring other dependencies
need a trusted prebuilt sandbox image; do not install untrusted dependencies on
the API host or remove isolation. Provider calls default to 180-second request timeouts
(`LLM_REQUEST_TIMEOUT_SECONDS`), 8192 output tokens (`LLM_MAX_OUTPUT_TOKENS`), and
LOW Gemini thinking (`LLM_THINKING_LEVEL`) to avoid spending the deadline on reasoning;
failures stop the task with diagnostic events instead of simulating progress.


Explicit model quota exhaustion skips retries against the same quota and moves
to the next configured provider. Other transient 429/5xx failures keep bounded
router backoff. Google SDK retries are disabled to avoid nested retry delays.
Execution reserves every provider request against the task model-call budget,
and provider errors appear immediately in Live Events. Cloud quota
limits still need to be managed in the owning Google Cloud project; code cannot
increase them. A successful planning call does not guarantee quota remains for
patch generation.

## Repair quality and publication

After before/after tests, a separate structured LLM review checks the actual diff
against the full request. Failed or unverified functional requirements block the
patch and feed concrete findings back into the bounded repair loop. Every review
provider request counts against the same model budget. A review outage fails closed.
The reviewer generates a conventional commit subject of at most 72 characters
from the implemented changes. The API shows it before approval and publication
uses it for both the commit and PR title. User prompts are never commit subjects.
Older saved tasks use a short filename-based fallback. Publication reserves
`gitresolve-agent/<readable-change>-<number>` atomically per repository, so retries
reuse their branch without showing task IDs or hashes in branch/commit names.
Existing exact-diff approval, Docker evidence and non-overwrite checks still apply.

Detected Streamlit UI repairs use `gitresolve-sandbox:streamlit`. Build it once:

```powershell
docker build -t gitresolve-sandbox:poc sandbox
docker build -f sandbox/streamlit.Dockerfile -t gitresolve-sandbox:streamlit sandbox
```

This profile includes pinned Streamlit 1.66 and an image-owned pytest gate.
Final tests must execute the original app entry point using AppTest; requested
checkbox interactions must exercise both states. The gate checks actual framework
output for HTML rendered as code and reports displaying literal Markdown markers.
Helper/AST tests alone cannot pass it. External model/network boundaries can be
mocked for deterministic UI tests; widgets and rendering must remain real.
Answer-toggle requests must actually hide/reveal answer labels, not just toggle a
widget. Report requests must render a representative Markdown sample containing
`Report formatting regression sample` in the actual report state. The repair prompt
supplies the fixture. Committed tests use normal project dependencies and literal
fixtures, without importing GitResolve-only modules. Reproduction runs only the
new regression files, so an unrelated existing test failure cannot count as proof.
All CPU/memory, network, filesystem and protected-path controls remain enforced.
AppTest simulates Streamlit, not a browser screenshot: visual design and responsive
layout still need human review. Other UI frameworks need their own testing profile;
this does not add browser verification for React or other frontends.


## Repair context attachments

The composer accepts up to four PNG/JPEG/WEBP images (5 MB each, 8 megapixels,
non-animated) and UTF-8 TXT/LOG/MD/JSON/YAML files (128 KB each), 10 MB total.
Authenticated `POST /api/v1/attachments?filename=...` accepts raw bytes and returns
an owner-scoped ID. Task creation optionally accepts `attachmentIds`; existing
prompt/issue payloads remain valid. Task retries must retain the same IDs.

Run the official ClamAV scanner locally before uploading:

```powershell
docker run -d --name gitresolve-attachment-scanner --restart unless-stopped --memory 1536m --cpus 1 -p 127.0.0.1:3310:3310 --mount source=gitresolve-clamav-db,target=/var/lib/clamav clamav/clamav:stable
```

The scanner downloads/updates its signatures and may need time to become ready.
Do not expose its unauthenticated TCP port publicly. Optional backend variables:
`ATTACHMENT_SCANNER_HOST`, `ATTACHMENT_SCANNER_PORT`, `ATTACHMENT_STORAGE_PATH`.
Missing/unavailable/erroring scanners reject uploads with 503; no bypass exists.
Keep antivirus signatures current. Scanning reduces risk and does not prove a file harmless.

Original bytes are scanned before image decoding. Images are signature-checked,
pixel-bounded, reduced to 1600px and re-encoded as PNG without metadata. Uploads
use random filenames, restrictive creation permissions and a private directory
outside public/static paths, default `.diagnostics/uploads`. Restrict that volume
with OS permissions. API and worker must share the same private upload volume;
separate Cloud Run/VM deployment needs shared private storage before enabling this
feature. The API sweeps expired files every ten minutes (24-hour retention).
Unreferenced upload metadata contains no source bytes and should have matching
Firestore TTL retention when deployed.

Text and vision-derived observations are bounded, redacted and labelled untrusted.
Image observations use configured models and count against the task call budget.
The resulting context is retained with the task so execution uses the same evidence.
Uploads never enter repository paths, command arguments or the Docker sandbox and
cannot bypass deterministic patch, test or approval checks. Removed composer files
are not sent with the task; their temporary server copies expire automatically.
