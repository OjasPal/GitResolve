# Speed milestone — measured results and validation

Date: 2026-11-06. Branch: `agent/workflow-optimization`. Milestone commit: use
`git log -1 --format=%h -- docs/speed-validation.md` after committing `speed`.
This report does not claim AL or graph implementation.

## What changed

- Listing benchmark selects **OjasPal explicitly**, deriving the user ID from the
  `users/<id>` document key; it neither writes live state nor prints its contents.
- Timing records actual SDK invocation separately from preparatory LLM events,
  per-stage known token usage and completeness, final stage flush, and actual
  Docker launches (including environment preparation). Nested captures and
  explicitly propagated background contexts are tested. Prewarm owns a separate
  context. Nested spans must not be added to infer total wall time.
- Repository/issue display pages have 15-second process-local caches scoped to
  identity and installation/repository. Pages load 30 upstream candidates per
  installation and return `nextPage`; inaccessible candidates never leak and an
  empty authorized page can still have a continuation. Permission checks run
  with at most four threads per installation. Upstream ETags support conditional
  revalidation. Installation tokens are scoped by installation/repos/permissions,
  bounded, in memory, and expired before their upstream deadline. Rejections
  invalidate caches; a rejected token is refreshed once, not served indefinitely.
- Locator hints avoid repeating full installation discovery for a known repo,
  but task/source/publication access rechecks GitHub metadata and the signed-in
  user's numeric identity and write permission. Cached display data never grants
  publication access. GET retries reduced from three to two attempts, with
  3-second connect/7-second read inactivity timeouts; writes remain non-retried
  except the existing single rejected-token refresh.
- Reuse immutable prepared environments by dependency/Python/image identity;
  cache original successful baselines by owner/installation/repo/SHA/actual
  prepared environment/command/verifier and framework options. Check environment
  integrity/identity before a hit. Never reuse a patched verification result.
- One bounded 60-second prewarm per worker process, no waiting queue, begins
  during plan review. It performs no edits or model calls and cannot publish or
  change task status. Cancellation, changed gate revision/base and rejection
  stop it. Existing wheel preparation stays wheel-only and runtime network-off.
  Its snapshot copy preserves permissions without redundant metadata copies.
- AppAccess no longer waits for repository discovery on every route. Identity
  checks run in the background; screens own skeleton/error/empty states. Existing
  TanStack Query supplies shared per-session caching and background refresh.
  Session changes clear data, and a late 401 cannot sign out a replacement session.
  Repository/issue pages have Load more controls. Existing Link prefetch, parallel
  dashboard requests and lazy Shiki loading were retained. No new UI dependency.

## Live GitHub listing timings

Same local identity, two passes per process, 18 visible repositories. Before
measurements were captured before optimization. After measurements use final
listing code. Warm means the process TTL cache is populated, not upstream HTTP
latency. These are direct gateway timings, not end-to-end browser/API timings.

| Operation | Before first | Before repeat | After first | After repeat |
| --- | ---: | ---: | ---: | ---: |
| Repositories | 19.600 s | 19.296 s | 11.433 s | 0.345 ms |
| Issues | 20.964 s | 20.535 s | 2.951 s | 0.062 ms |

Raw: `speed-before-listings.json`, `speed-after-listings.json`. Cold repository
discovery remains over the 3-second target; only the warm display path meets it.
TTL display data can be stale for 15 seconds; mutations/source reads remain fresh.

## Docker preparation before/after

These measurements precede the later baseline-identity tightening and compare
identical real fixture executions before and after prepared-environment reuse.
The wheelhouse was already warm. “First” is cold **process environment cache**,
not a cold package download or cold Docker image.

| Fixture | Before first/repeat | After first/repeat |
| --- | ---: | ---: |
| stdlib | 2.171 / 1.656 s | 1.968 / 1.500 s |
| requirements | 5.593 / 5.531 s | 5.031 / 1.938 s |
| Streamlit | 11.311 / 11.516 s | 9.436 / 2.515 s |

Raw: `speed-before-sandbox.json`, `speed-after-sandbox.json`. Dependency fixtures
drop from two Docker launches per execution (prepare + test) to one when warm.
No runtime container is reused. Full regression-before/after and review remain.

## Complete scripted repair comparison

Final-code controlled **cache-off/on** comparison, not a historical live-model
before/after run. The trivial fixture adds missing `re`; the multi-file behavior
fixture repairs an imported arithmetic helper and declares a wheel dependency.
Both require a failing original regression and a passing patched full suite.
There are two scripted model actions per repair, **zero live calls and zero actual
input/output tokens**. Model response time is not simulated.

| Fixture/cache | First total | Repeat total | First action, repeat | Docker launches first/repeat |
| --- | ---: | ---: | ---: | ---: |
| Trivial, off | 6.319 s | 4.497 s | 1.569 s | 3 / 3 |
| Trivial, on | 4.567 s | 3.305 s | 0.226 s | 3 / 2 |
| Multi-file behavior, off | 17.410 s | 16.502 s | 5.502 s | 6 / 6 |
| Multi-file behavior, on | 8.839 s | 4.296 s | 0.611 s | 4 / 2 |

“First action” above is the scripted proposal boundary after setup/baseline,
not Start-to-first-real-model latency. Raw per-stage timings are in
`speed-repair-comparison.json`; the AL proposal includes the warm breakdown.
Two observations per condition cannot establish a reliable p50/p95. AL's eval
will use repeated labeled cases and shadow full verification.

## Frontend and tunnel limits

The pre-change production build passed. Initial PowerShell HTTP measurements:
production warm routes 3.70–4.03 ms; development warm routes 92–148 ms, with first
development requests 0.88–3.01 s. These are HTTP response measurements, not clicks,
hydration, dropdown paint or browser interaction timings.

Final build, Python HTTP client, latest warm route observations:

| Route | Production | Development |
| --- | ---: | ---: |
| dashboard | 15.649 ms | 106.231 ms |
| settings | 14.926 ms | 82.500 ms |
| tasks | 15.062 ms | 98.551 ms |
| tasks/new | 14.618 ms | 116.233 ms |

The HTTP clients and warm-up differ across the initial/final samples; do not claim
a before/after navigation speedup from those numbers. The meaningful comparison
is production versus development within each run. Use a production build for
teammate previews; instructions are in `frontend/README.md`.

The saved shared backend tunnel was unavailable on both health attempts, and
local port 8010 was unavailable. Port 8000 returned 200 (about 2.08 s using
`localhost` in this client). No configured HTTPS frontend origin was present in
the active backend settings. Therefore **no successful tunnel-vs-local latency
comparison or teammate frontend navigation measurement is claimed**. No tunnel
was restarted or exposed. Raw observations: `speed-after-frontend-http.json`.

Rendered-browser inspection was attempted but app approval timed out. Browser
navigation, hydration, cached-dropdown <300ms and responsive visual QA remain
unverified. Node tests verify loading/error markup and cache behavior, not paint.

## Gemini schema probe

`agent-action-probe.json`: the discriminated union failed local SDK validation
(`discriminator` and `oneOf` unsupported on this path). No usage was returned;
do not claim a billed zero-token response.

`agent-action-flat-probe.json`: flat enum + typed args accepted and post-validated
through the existing ModelRouter. One real Gemini request, **199 input / 55
output tokens**, **15.426 s**. The price estimate in the raw provider record is
not a verified current-price claim. No private code or task contents were sent.
Commit 3 will use the flat format, with per-tool post-parse validation.

## Validation and exact commands

All Python commands ran from the repository root with `$env:ENVIRONMENT='test'`.
Repository fixture code ran only in reviewed Docker containers; unit drivers
mock providers/GitHub. Real Docker tests assert read-only env mounts and denied
network. The full suite was run once for this milestone.

| Command | Result |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest -q -ra --tb=short` | **428 passed, 2 skipped**, 107.82 s |
| `.venv/Scripts/python.exe -m pytest backend/tests/unit/test_speed_caches.py backend/tests/unit/test_github_real.py backend/tests/unit/test_speed_timing.py -q --tb=short` | **43 passed**, 1.08 s, after final gateway error-contract correction |
| `.venv/Scripts/python.exe -m pytest backend/tests/unit/test_speed_caches.py backend/tests/sandbox/test_speed_cache_integration.py backend/tests/sandbox/test_prepared_environments.py -q -ra --tb=short` | **24 passed**, 42.34 s; real Docker, no skips |
| `node --test tests/speed.test.mjs` (frontend) | **5 passed** |
| `npm run lint` (frontend) | passed, no warnings |
| `npm run build` (frontend) | passed; existing metadataBase warning only |
| `git diff --check` | passed |

The full run preceded the final small gateway error-contract fix and its new
test; the 43-test affected set then passed. It was not rerun. Full-suite skips:
Firestore client package unavailable; loopback Firestore emulator unavailable.
Initial synthetic repair benchmarking failed due to Windows CRLF fixture writes;
fixtures were corrected to LF and the full comparison completed. A prior cleanup
logging warning was fixed before the full suite. No failing result is counted
as a passing benchmark.

Reproduction commands:

```powershell
.venv/Scripts/python.exe scripts/benchmark_github_listings.py --github-login OjasPal --output docs/speed-after-listings.json
.venv/Scripts/python.exe scripts/benchmark_sandbox_speed.py --output docs/speed-after-sandbox.json
.venv/Scripts/python.exe scripts/benchmark_repair_speed.py --output docs/speed-repair-comparison.json
.venv/Scripts/python.exe scripts/benchmark_frontend_http.py --output docs/speed-after-frontend-http.json
.venv/Scripts/python.exe scripts/profile_local_run.py --github-login OjasPal --repository OjasPal/PrompCraft --issue 2 --output docs/prompcraft-historical-timing.json
.venv/Scripts/python.exe scripts/probe_agent_action.py --output docs/agent-action-probe.json
.venv/Scripts/python.exe scripts/probe_agent_action.py --flat --output docs/agent-action-flat-probe.json
```

Do not repeat live probes merely to reproduce documentation. Before listing runs
require the pre-optimization implementation; current commands produce after data.
The sandbox comparison script performs isolated original-code fixture tests;
the repair comparison explicitly toggles process caches. No live task, branch,
commit or PR was created by any benchmark.

## Remaining verification

No live teammate authorization/revocation exercise, successful tunnel measurement,
browser timings, full historical PrompCraft run or live end-to-end repair tokens
are available. These limitations do not relax server-side checks. AL adversarial
shadow evaluation, static-proof receipts, SQLite recovery, graph parity and real
graph scenarios belong to the next two approved milestones. Firestore saver and
reaper-driven graph resume remain Phase 2B.
