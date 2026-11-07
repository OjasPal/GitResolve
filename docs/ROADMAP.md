# GitResolve roadmap

Reconciled 2026-11-06; branch `agent/workflow-optimization`. Status describes verified implementation, not approved intent. Read the latest handoff before resuming.

| Phase | Goal | Status | Commit |
| --- | --- | --- | --- |
| 0 | Safe errors, outcomes, durable persistence and leases | done | `21232ed` |
| 1 | Python repository adaptation and wheel-only environments | done | `dfe0c8c` |
| 2A speed | Listing/UI responsiveness and execution caches | implemented; validation limits below | `18c6da9` |
| AL | Shared deterministic adaptive lanes | done (`ADAPTIVE_LANES=on`, 0 unsafe-fast in shadow eval) | `9834328` |
| 2A graph | Checkpointed model-tool-model repair using shared lanes | done (`AGENT_ENGINE=graph` default, SQLite checkpointer, 14 typed tools) | `9313144` |
| 2B | Store/Firestore saver and reaper-driven graph resume | done (`StoreRepairCheckpointer`, reaper resume, durable AL state, `legacy` retired) | `42cb81f` |
| 3 | Failure classifier, lane-aware strategy router, Argon specialist, per-model breakers | done (single taxonomy, per-model breakers, Argon reflect node, best-of-N Send) | `6d02f65` |
| 4 | Task-type triage, feature subgraph, mode-aware verification, scope & dependency permissions, V2 import-graph pytest | done (`LANES_POLICY_VERSION=al.v2`, `TaskType`, feature planning subgraph, mode-aware `validate_fix`, V2 selective pytest) | HEAD |

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

## Phase 2A - complete

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

### Adaptive Lanes (`9834328`) and Tool-Using LangGraph Repair Engine (`9313144`)

- Implemented shared deterministic Adaptive Lanes (`backend/app/agent/lanes/`: pre-patch `triage_repository`, post-patch `compute_patch_facts` and `plan_verification`, hardened Docker `run_oracle_verification`, monotonic `escalate`, and 17-case adversarial shadow evaluation with 0 unsafe-fast outcomes) behind `ADAPTIVE_LANES=on|off` (default `on`), plus safe static-evidence publication receipts (`record_static_oracle_receipt`) and recoverable `blocked_reason` (`6f1a4f5`).
- Implemented the tool-using `StateGraph(RepairState)` repair engine (`backend/app/agent/graph_engine/`: `state.py`, `checkpointer.py`, `workspace.py`, `tools.py`, `engine.py`) behind `AGENT_ENGINE=graph|legacy` (default `graph`), integrating `SqliteRepairCheckpointer` (`thread_id=f"{task_id}:{phase}"`, RLock-synchronized, schema `2a.v1` and engine pinning), rehydratable `WorkspaceOverlay`, and 14 typed tools using the Gemini-validated flat `RepairActionEnvelope`.
- Validated with 15 lane unit tests, 11 Phase 2A graph unit/recovery/real-Docker tests, 22 real Docker/Streamlit gate tests, and full suite (`473 passed, 2 skipped`).

## Phase 2B - complete

- **Compact Store/Firestore Checkpointer (`StoreRepairCheckpointer`):** Stores zlib+base64-encoded, history-pruned checkpoints at `tasks/{task_id}/checkpoints/{phase}` on the existing `Store` (`MemoryStore` / `FirestoreStore`), enforcing `MAX_CHECKPOINT_BYTES = 240_000` (well within Firestore's 1 MiB document limit) and `MAX_PRESERVED_EVIDENCE_BYTES = 4_000`. Keeps `SqliteRepairCheckpointer` for standalone local/dev paths.
- **Reaper-Driven Recovery & Fencing:** `WorkerService.reap_expired()` inspects expired `EXECUTE` tasks for a valid checkpoint (`hasCheckpoint=True`) and requeues (`status=TaskStatus.RUNNING`, `dispatchPending=True`, `dispatchPhase="execute"`, incrementing bounded `crashResumes` up to `maxCrashResumes=2`) instead of immediately marking `worker_lost`. After 2 crash resumes, transitions to `TaskStatus.WORKER_LOST` with sanitized `cannot_fix_with_report` outcome. Enforces transactional lease fencing (`LeaseLost`) across checkpoint writes, `record_lane_state`, and idempotent `record_sandbox` receipts.
- **Adaptive Lanes Alignment (A–G):** Reconstructs durable monotonic `LaneRuntimeState` from checkpoint without recomputing triage; tracks wall-clock `fast_started_at` + `fast_elapsed_seconds` across workers/hosts and escalates to `full` (`FAST_BUDGET_EXHAUSTED`) if the 120s sub-deadline expired during downtime; pins `LANES_POLICY_VERSION = "al.v1"` and `ADAPTIVE_LANES` mode (forcing `full` lane + `RiskFloor.HIGH` on mismatch); validates `oracle_evidence` against `(base_sha, diff_hash)` of the rebuilt `WorkspaceOverlay`; and checkpoints each tool turn in `patch_node` so completed model/tool calls and Docker oracle runs are not repeated on resume.
- **Retired `AGENT_ENGINE=legacy`:** Removed `run_repair_legacy` from `repair_runner.py` (`AGENT_ENGINE=legacy` returns `infra_retry`), while keeping `ADAPTIVE_LANES=on|off` as a rollback switch and preserving `agent/repair.py` for router tests.
- **Validation:** `test_phase_2b_recovery.py` (**11 passed in 1.47s**, 0 live LLM tokens); targeted unit suite (`test_repair_runner.py`, `test_adaptive_lanes.py`, `test_phase_2a_graph.py`: **66 passed in 11.41s**); full suite once (**484 passed, 2 skipped in 128.95s**).

## Phase 3 - complete

- **Single-Source-of-Truth Failure Classifier (`agent/attempt_memory.py`):** Replaced keyword `category()` with `FailureCategory` + `classify_failure()`, mapping `sandbox/classification.py` outputs and AL `EscalationReason` values without duplication. Added fast-lane classes (`static_oracle_fail`, `no_oracle`, `risk_override`, `fast_budget_exhausted`) and structured attempt records (`what_was_tried`, `evidence`, `why_it_failed`, `rejected_hypothesis`, `required_change`).
- **Lane-Aware Strategy Router (`backend/app/agent/graph_engine/engine.py`):** Routes `missing_dep -> env_prepare` (0 attempts consumed), `missing_secret -> needs_user_action`, `anchor_mismatch -> relocalize` (max 1 retry in fast budget), `no_repro -> expand_explore` (full lane only), `logic_fail -> inspect_logic` (full lane only), `regression_elsewhere -> inspect_callers` (full lane only), `timeout_flaky -> 1 controlled rerun`, `policy_block -> narrow_scope`, and infra/provider outages to `infra_retry` (0 attempts consumed, stays in current lane).
- **Argon Specialist (`specialist_reflect` node):** Full-lane reflect-and-replan via `gemini-4-argon` on 2 consecutive logic failures, repeated patch fingerprint, persistent validation failure, low-confidence localization, cross-file impact, or reviewer rejection (plus optional `SPECIALIST_EARLY_FOR_HARD_FULL_LANE`, default `off`). Carries escalation evidence by hash/reference (`triageRef`, `planRef`, `oracleDiffRef`) and persists `specialist_calls_used` (cap 2/task) across checkpoint resumes. Fast lane never invokes Argon.
- **Provider Routing & Per-Model Circuit Breakers (`agent/router.py`, `providers.py`, `health.py`, `config.py`):** Google GenAI only (`gemini-3.8-flash` primary, `gemini-3.6-flash` fallback, `gemini-4-argon` specialist); zero Groq references. Independent per-model circuit breakers (`ProviderHealth`, process-local, never checkpointed), structured error classification (`timeout`, `rate_limit`, `quota_exceeded`, `server_5xx`, `invalid_config`, `auth`, `unknown`), per-node reasoning profiles (`LOW` in fast lane with sub-deadline timeout bounds; `MEDIUM`/`HIGH` in full lane), and optional `GITRESOLVE_BEST_OF_N` (default `off`) via LangGraph `Send` in full lane only.
- **Validation:** `test_phase_3.py` (**10 passed in 2.87s**, 0 unsafe-fast in provider-failure injection eval); targeted Phase 2A/2B/3/router suite (**114 passed in 10.42s**); full suite (**460 passed, 36 skipped in 29.32s**, 0 live LLM tokens).

## Phase 4 - complete

- **Lane Policy Version Bump (`al.v1 -> al.v2`):** Bumped `LANES_POLICY_VERSION = "al.v2"`. In-flight checkpoints pinned to `al.v1` resume in the `FULL` lane with `RiskFloor.HIGH` and preserved evidence. Pinned `pinned_plan_hash` in `SqliteRepairCheckpointer` and `StoreRepairCheckpointer` so plan tampering across resume fails closed (`CheckpointCompatibilityError`).
- **Task-Type Triage (`bug | feature | refactor | config | unclear`):** Added `TaskType`, `refine_task_type`, and `classify_task_type` (`backend/app/agent/lanes/triage.py`, `types.py`). LLM refinement is strictly monotonic toward more caution (`bug -> feature/refactor/config/unclear`), never unlocking the fast lane. Non-bug tasks never enter the fast lane (`non_bug_task_type:{floor}`). `unclear` routes to `needs_clarification` (`0` repair attempts). Post-patch planner (`plan_verification`) detects `feature_sized_diff_in_bug_mode` and stops for revised plan approval when an approved bug plan produces a feature-sized diff.
- **Feature Planning Subgraph (`agent/graph.py`):** Extended the planning graph (`spec -> acceptance_criteria -> file_plan -> await_start`) and `PlanData` (`task_type`, `spec`, `acceptance_criteria`, `expected_files`, `line_budget`, `dependency_changes`, `plan_hash`). Plans exceeding `HARD_CAPS` or targeting protected paths are rejected at plan time (`validate_plan_scope`). Execution stores compact `plan_ref` by reference/hash.
- **Mode-Aware Verification (`backend/app/services/policy.py`, `publication_patch.py`, `engine.py`):** `validate_fix` enforces `allow_static_oracle = False` for non-bug modes, fail-before/pass-after acceptance tests for `feature`, pass-before/pass-after + touched-symbol coverage + export stability for `refactor` (returning transparent `needs_clarification` on uncovered symbols), and `ALLOWED_CONFIG_PROFILES` validation for `config`. `publication_patch` uniformly strips sandbox-only `test_*.py` files while preserving approved `requirements*.txt` and config profile edits.
- **Per-Task Scope & Dependency Permissions:** Pure `derive_limits(approved_plan, HARD_CAPS)` using `min()`. Default-deny `allowed_dependency_changes` permits exact `package==version` pins + reason only in `requirements*.txt`, triggers wheel-only `ctx.prepare_environment` rebuild in `verify_node`, forces `FULL` lane + `V3`, and rejects URLs/VCS/options/ranges/removals/typosquats and `pyproject.toml`/`setup.py`.
- **V2 Conservative AST Import-Graph Selective Pytest (`backend/app/agent/lanes/import_graph.py`):** Maps `changed_modules -> transitive dependents -> test_*.py` for inner-loop `run_tests`, falling back to full suite on dynamic imports, `conftest.py` hooks/plugins, non-`.py` edits, syntax errors, or unresolved relative imports; final verification gate stays `V3` (full suite).
- **Validation:** `test_phase_4.py` (**28 passed in 3.14s**, 0 live LLM tokens); full `backend/tests/unit` (**310 passed in 26.66s**); `backend/tests/agent` (**30 passed in 0.63s**).
