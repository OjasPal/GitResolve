# Handoff — 2026-11-07 — Phase 2B Store Checkpointer & Reaper-Driven Resume

## Branch and commits
- Branch: `agent/workflow-optimization`
- Prior commits: Phase 0 `21232ed`, Phase 1 `dfe0c8c`, speed `18c6da9`, docs `7aceb9b`, recoverable `blocked_reason` `6f1a4f5`, adaptive lanes `9834328`, Phase 2A graph `9313144`.

## Working state and validation
- Implemented `StoreRepairCheckpointer` (`FirestoreRepairCheckpointer`) on `Store` at `tasks/{task_id}/checkpoints/{phase}` with history pruning, `MAX_CHECKPOINT_BYTES = 240_000`, and transactional lease fencing (`LeaseLost`), while keeping `SqliteRepairCheckpointer` for local/dev.
- Updated `WorkerService.reap_expired()` to requeue expired `EXECUTE` tasks with a valid checkpoint (`crashResumes` bounded to 2, then `TaskStatus.WORKER_LOST` with sanitized `cannot_fix_with_report` outcome). Added fenced `record_lane_state` and idempotent `record_sandbox` per `(baseSha, diffHash)`.
- Aligned Adaptive Lanes across resume (A–G): durable `LaneRuntimeState.from_checkpoint` without recomputing triage; compact `preservedEvidence` (`<= 4000` bytes); wall-clock `fast_started_at`/`fast_elapsed_seconds` with downtime sub-deadline escalation (`FAST_BUDGET_EXHAUSTED`); `LANES_POLICY_VERSION = "al.v1"` + `ADAPTIVE_LANES` pinning (mismatch forces `full` lane + `RiskFloor.HIGH`); `is_oracle_evidence_valid` `(base_sha, diff_hash)` check; and per-tool-turn checkpointing in `patch_node`.
- Retired `run_repair_legacy` (`AGENT_ENGINE=legacy` returns `infra_retry`); preserved `ADAPTIVE_LANES=on|off` rollback switch and `agent/repair.py`.
- Validation (0 live LLM tokens):
  - `.venv/Scripts/python.exe -m pytest backend/tests/unit/test_phase_2b_recovery.py -q --tb=short` -> **11 passed in 1.47s**
  - `.venv/Scripts/python.exe -m pytest backend/tests/unit/test_repair_runner.py backend/tests/unit/test_adaptive_lanes.py backend/tests/unit/test_phase_2a_graph.py -q --tb=short` -> **66 passed in 11.41s**
  - `.venv/Scripts/python.exe -m pytest --ignore=backend/tests/fixtures -q --tb=short` -> **484 passed, 2 skipped in 128.95s**

## Remaining work
- Phase 3: Deterministic failure classifier, strategy router, Argon specialist escalation, and per-model circuit breakers.
- Phase 4–5: Feature/refactor/config task types, import-graph V2 selective pytest, and live evaluation harness + safety audit.

## Gotchas
- `StoreRepairCheckpointer` and `SqliteRepairCheckpointer` prune to the latest 2 checkpoints per namespace and enforce `RLock` synchronization for LangGraph's `BackgroundExecutor`.
- Crash resumes (`crashResumes` and `crash_resume` events) do not increment `attempts.used` or append `EscalationReason` entries.
- Human approval gates (`awaiting_start`, `awaiting_approval`) remain backend states in `WorkerService`; never use `langgraph.types.interrupt()`.
