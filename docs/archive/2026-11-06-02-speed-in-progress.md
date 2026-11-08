# Handoff — 2026-11-06 — speed milestone in progress

Branch: `agent/workflow-optimization`. No speed/AL/graph milestone commit yet. All pending code from this session is speed work; preserve it. No push or merge authorized.

## Authorization and required sequence

The kickoff authorizes implementation and local commits, superseding the old documentation-only checkpoint. Exactly three milestones: `speed`, `AL`, `2A-graph`. Preserve `docs/prompts/AL.md` and `docs/prompts/PHASE-2A.md` verbatim in speed. STOP A is still required after speed and the real schema probe: provide measured timing tables, AL proposal, shared-module graph design, probe result; wait for user approval before AL/graph implementation.

## Saved work (unreviewed until milestone validation)

- Timing capture, actual provider invocation hook, final-stage flush and actual Docker launch spans; explicit OjasPal benchmark selection from document keys.
- Scoped TTL/token/ETag caches, parallel permissions, paged repository/issue endpoints and fresh task/publication authorization.
- Immutable prepared-environment reuse, original-baseline cache and bounded plan-review prewarming; every actual execution remains a fresh hardened container.
- Nonblocking AppAccess, shared client queries, lazy repository/issue pages and route loading state.
- Scripts and raw timing JSON files under `scripts/` and `docs/`; no private repository contents or credentials in reports.

## Evidence so far

- Listing baseline: repositories 19600.400/19295.848 ms; issues 20964.311/20535.194 ms. Optimized first/warm: repositories 10438.481/0.310 ms; issues 3466.479/0.088 ms. Later edits still need final validation.
- Warm prepared fixture before/after: requirements 5531/1938 ms; Streamlit 11516/2515 ms. Original wheels were already warm; do not label these cold wheel-download results.
- Production warm HTML response about 4 ms versus development 92–148 ms. These are HTTP, not browser/hydration measurements. Browser access approval timed out; no browser result obtained.
- SDK rejected discriminated union before a successful network probe. Approved flat enum + args fallback succeeded through ModelRouter: Gemini reported **199 input / 55 output tokens**, 15425.592 ms. See the two probe JSON reports. No additional live probe is needed.
- Local OjasPal/PrompCraft issue 2 lookup returned no matching historical run.
- Targeted runs: 13 timing/recovery/router tests; 23 GitHub boundary tests; 33 timing/repair/router tests; 16 timing/cache tests passed. Full suite NOT run yet. Original frontend production build passed; updated frontend still needs build and final lint.

## Next work

1. Run `scripts/benchmark_repair_speed.py --output docs/speed-repair-comparison.json` in the reviewed Docker environment. This was not executed: automatic approval review hit the usage limit. User subsequently returned and requested continuation. Do not count the failed launch as a test.
2. Review cache correctness (including locator versus complete-list caches, cancellation, refcounts and cache-hit provenance), add missing authorization/pagination/real-Docker tests, finish UI lazy loading and validation.
3. Finish measurements/report; clearly distinguish measured before/after from reconstructed cache-off/on comparisons and missing browser/tunnel checks.
4. Run targeted tests, then full suite at most once for speed. Write AL proposal and graph summary, update ROADMAP/AGENTS/handoffs, commit `speed`, then STOP A.

## Gotchas

- Existing dev server uses port 3000. A temporary production server was launched on 3101 (session 79461); do not stop unrelated user servers. A second dev launch on 3102 exited because the existing dev server owns its lock.
- Sandbox restrictions blocked Docker/local test-client sockets and frontend build directory creation; escalated runs worked before usage exhaustion. No safety gate was bypassed.
- The original user instructions require exact-diff approval before target-repository publication, which is separate from authorized local GitResolve milestone commits.
- No AL lane modules, graph execution replacement or engine flags exist yet.
