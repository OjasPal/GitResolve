# GitResolve roadmap

Reconciled 2026-11-06; branch `agent/workflow-optimization`. Status describes verified implementation, not approved intent. Read the latest handoff before resuming.

| Phase | Goal | Status | Commit |
| --- | --- | --- | --- |
| 0 | Safe errors, outcomes, durable persistence and leases | done | `21232ed` |
| 1 | Python repository adaptation and wheel-only environments | done | `dfe0c8c` |
| 2A speed | Listing/UI responsiveness and execution caches | implemented; validation limits below | `18c6da9` |
| AL | Shared deterministic adaptive lanes | done (`ADAPTIVE_LANES=on`, 0 unsafe-fast in shadow eval) | `feat: adaptive lanes` |
| 2A graph | Checkpointed model-tool-model repair using shared lanes | design ready; awaits AL | Not implemented |
| 2B | Firestore saver and reaper-driven graph resume | not started | Explicitly deferred |

## Phase 0

**Decisions and why:** Extend existing public sanitization boundaries; safe incident references instead of raw errors. Preserve status/failure enums and exact-diff approval. Bounded Windows rename retries and staged memory commits protect durability. OS ownership locking prevents unsafe multi-process local state use. Throttle heartbeat writes without extending memory-only expiry.

**Contracts changed:** Task API adds `outcome` and `userMessage`; structured outcomes carry reason, explanation, evidence and next step. AgentRunner accepts outcomes. Infrastructure retries are bounded requeues, not terminal outcomes. Backend services retain status ownership.

**Tests added/updated:** `test_workflow_optimization.py`, persistence/recovery tests and router/graph expectations cover credential/path redaction, rollback, cross-process ownership, durable heartbeat/cancel/lease behavior and outcome mappings.

**Validation:** Full run: 337 passed, 3 failed, 27 skipped. Three obsolete expectations were corrected; affected 10-test run passed. Final focused run: 42 passed. Real sandbox/GitHub-Docker run: 57 passed after Docker started. Firestore and then-unavailable ClamAV checks remained unverified. No clean full-suite rerun was claimed. Exact commands: [workflow-optimization.md](validation/workflow-optimization.md).

## Phase 1

**Decisions and why:** Static AST/TOML profiling only. Isolated pip downloads binary wheels from a fixed index; transitive metadata is validated before resolution. Reject URL/VCS/local/editable/index overrides and source builds. Install offline inside disposable Docker containers. Prepared venvs use Docker volumes because Windows bind mounts were too slow; runtime mounts are read-only. Cache wheels by dependency contents, Python version and base-image ID with integrity manifests.

**Contracts changed:** `ExecutionRequest.prepare_dependencies`; repository profile, deterministic baseline classifier, framework adapters and ranked numbered context. Missing packages/secrets never establish reproduction. Infrastructure/provider failure does not charge repair attempts; incurred model calls remain counted. See [sandbox README](../sandbox/README.md) for supported formats and limits.

**Tests added:** Five stdlib/requirements/pyproject/Flask/Streamlit repositories plus missing-dependency, missing-secret and collection-error fixtures. Profiler, unsafe dependency rejection, cache integrity, ranked context, attempt accounting and real prepared-environment tests. Runtime assertions cover denied network and read-only mounts; existing Streamlit AppTest gates remain.

**Validation:** 48 targeted unit tests passed. Eight real Docker fixtures passed in 153.15 seconds. Full suite: 407 passed, 1 failed, 2 Firestore skips in 123.91 seconds. Fixed Windows short-path normalization; 39 affected tests then passed. Full suite was run once, not repeated. Exact commands: [phase-1-validation.md](validation/phase-1-validation.md).

## Phase 2A - partial

### Speed milestone

- Replaced preliminary timing with SDK invocation, final-stage flush, per-stage known usage and actual Docker launch instrumentation. Explicit owner selection fixes the listing benchmark.
- Scoped TTL/ETag display caches, scoped expiring installation tokens, bounded permission concurrency, lazy 30-candidate pages and fresh task/source/publication authorization. Backend and client caches never grant write access.
- Read-only prepared environment reuse, original-only baseline caching and bounded cancellable plan-review prewarming. Patched tests still run in a fresh hardened container; wheels remain wheel-only.
- Nonblocking AppAccess, existing TanStack Query for per-session stale-while-revalidate data, lazy pages and route skeleton. No new frontend dependency.
- Live repeated listings: repositories **19.296 s -> 0.345 ms**, issues **20.535 s -> 0.062 ms** at the gateway process cache. Cold repository discovery remains **11.433 s**. These are not browser timings.
- Scripted warm repair cache-off/on: trivial **4.497 -> 3.305 s**, multi-file behavior **16.502 -> 4.296 s**; Docker launches **3 -> 2** and **6 -> 2** respectively. Zero live model tokens in these fixtures.
- Gemini schema probe: union rejected locally; flat action envelope accepted, **199 input / 55 output tokens**, 15.426 s. Graph will use flat args with per-tool post-validation.
- Full suite once: **428 passed, 2 Firestore skips**. Final gateway error-contract correction followed by **43 affected tests passed**; no repeated full suite. **24** targeted cache/real-Docker tests passed; frontend **5 tests**, lint and build passed.
- Browser access approval timed out; no browser navigation/hydration/dropdown timing claim. Saved shared backend tunnel unavailable; no successful teammate tunnel comparison. Historical PrompCraft issue 2 run not found for the selected identity.
- Exact commands, raw results, caveats: [speed-validation.md](validation/speed-validation.md). AL proposal: [adaptive-lanes-proposal.md](design/adaptive-lanes-proposal.md). Graph design: [phase-2a-graph-design.md](design/phase-2a-graph-design.md).
- Prior documentation checkpoint is `e6d9d4e`. Speed commit is `18c6da9`. Original prompts are preserved verbatim under `docs/archive/`.

### Approved decisions and sequence

1. **Speed first, separate commit, report measurements before graph work.** First instrument and benchmark. Then bounded first-page/lazy listing, scoped token/TTL/ETag caches, limited parallel calls and nonblocking AppAccess shell. Preserve authorization. Inspection found exhaustive pagination, serial repository permission checks, repeated discovery before issue listing, uncached token minting, three 15-second GET attempts and shell blocking. These are code findings, not measured attribution of the reported latency.
2. **Real Gemini schema probe first.** Test one structured discriminated-union AgentAction through ModelRouter before building on it. If rejected/unreliable, use a flat action enum plus args object with Pydantic post-parse validation. Record actual usage, not scripted token estimates.
3. **SQLite only in 2A.** Defer Firestore saver and reaper-resume to 2B. Preserve backend plan/execute/PR phases, statuses and human gates; no `interrupt()`. Checkpoint thread ID: `task_id:phase`.
4. **Rehydratable workspace:** pinned snapshot plus cumulative validated edits; text reads from overlay. Every execution uses a fresh hardened `ctx.execute` container with overlay patch. No persistent runtime container.
5. **Graph:** intake/triage -> repo_profile -> env_prepare -> baseline -> explore/tool loop -> reproduce -> patch/tool loop -> static_check -> verify -> review -> finalize. Failures route back with classified evidence, not blind regeneration.
6. **State/tools:** typed bounded state with message/attempt reducers, task/base/profile/environment, hypotheses/evidence, localization, edits, budgets, provider metadata and outcome. Large logs/files belong in artifacts. Registry: list_tree, grep, numbered read_file, AST find_symbol/find_references, run_tests, run_check, narrowly sandboxed run_snippet, exact/whitespace/range edit_file, create_file, git_diff, ask_user, report_blocker, finish. Validate paths/symlinks/protected files/size; malformed actions return structured feedback. Every provider call uses ModelRouter.
7. **Configurable budgets:** proposed ceilings 240 steps, 120 tools, 100 provider calls including retries/review, 300K input/40K output tokens, existing 30-minute deadline and six strategies. Check leases each step; bounded action/evidence events, never hidden reasoning. Report actual tokens in test runs.
8. **Execution speed:** cache immutable prepared envs and eligible baselines by repository/SHA/env/command/verifier. Authorized, bounded background prewarming on selection; overlap with exploration. Combine checks only where gates allow; cheaper safe snapshot copying. Baseline caches never replace patched verification.
9. **Rollback:** `AGENT_ENGINE=graph|legacy`, engine/schema pinned per phase. Check references before deleting `agent/repair.py` or old state fields: router tests still reference the prototype.

### Still pending

STOP A approval, then commit 2 (`AL`) and commit 3 (`2A-graph`). Implement graph-agnostic lane modules behind `ADAPTIVE_LANES`, deterministic oracle/planner and safe static-evidence publication receipts, monotonic escalation and adversarial shadow evaluation. Default lanes on only with zero unsafe-fast cases. Graph nodes must reuse those modules; add StateGraph, SQLite recovery, tools/reducers/budgets and phase-pinned `AGENT_ENGINE`. Run the specified NameError/CSV-export, MCQGen, eligible PrompCraft-shape, escalation, crash and legacy-parity scenarios. Live teammate/browser/tunnel verification remains explicitly unverified; Firestore saver/reaper-resume stays 2B.

Targets, not measured achievements: end-to-end warm listings around 3 seconds, first real action around 10 seconds after Start, tiny live fixes within minutes. Process cache and scripted fixture measurements do not establish those live targets. No execution graph, checkpoint schema or adaptive lane implementation exists yet.

The 2026-11-06 kickoff supersedes the old “do not change code” checkpoint: implementation and local milestone commits are authorized. No push or merge. STOP A still requires the user's approval after speed/probe/design reporting before implementing AL/graph. The later request to continue after usage limits did not remove that gate.
