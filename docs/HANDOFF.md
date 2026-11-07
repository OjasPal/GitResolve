# Handoff — 2026-11-07 — Phase 3 Classifier, Strategy Router, Argon Specialist & Breakers

## Branch and commits
- Branch: `agent/workflow-optimization`
- Prior commits: Phase 0 `21232ed`, Phase 1 `dfe0c8c`, speed `18c6da9`, Adaptive Lanes `9834328`, Phase 2A `9313144`, Phase 2B `42cb81f`.

## Working state and validation
- Implemented single-taxonomy failure classifier (`FailureCategory`, `classify_failure` in `agent/attempt_memory.py`) mapping `sandbox/classification.py` and AL `EscalationReason` values, plus structured `AttemptMemory` (`what_was_tried`, `evidence`, `why_it_failed`, `rejected_hypothesis`, `required_change`, `compact_trajectory`).
- Implemented lane-aware strategy router edges in `backend/app/agent/graph_engine/engine.py`: `missing_dep -> env_prepare` (0 attempts), `timeout_flaky` controlled rerun, full-lane-only exploration/caller expansion, fast-lane 1x `anchor_mismatch` retry within fast budget, and pause-in-lane (`infra_retry`, 0 attempts) on infra/provider outages.
- Added full-lane `specialist_reflect` (`gemini-4-argon`) node with compact reference-only trajectory (`triageRef`, `planRef`, `oracleDiffRef`), persisted per-task cap (`MAX_SPECIALIST_CALLS_PER_TASK = 2`) surviving checkpoint resume, and optional `SPECIALIST_EARLY_FOR_HARD_FULL_LANE` (default `off`). Fast lane never calls Argon.
- Upgraded `ProviderHealth`, `ModelRouter`, `GoogleGenAIProvider`, and `NODE_REASONING_PROFILES`: Google GenAI only (0 Groq refs), process-local per-model circuit breakers (open primary breaker never blocks specialist; open specialist breaker never blocks fast/Flash chain), `UnknownProviderError` fallback, per-node `thinking_level`/timeout bounds, and optional full-lane `GITRESOLVE_BEST_OF_N` (`Send`, default `off`).
- Validation (0 live LLM tokens):
  - `.venv/Scripts/python.exe -m pytest backend/tests/unit/test_phase_3.py -q --tb=short` -> **10 passed in 2.87s** (0 unsafe-fast in AL failure-injection eval)
  - `.venv/Scripts/python.exe -m pytest backend/tests/unit/test_repair_runner.py backend/tests/unit/test_phase_2a_graph.py backend/tests/unit/test_phase_2b_recovery.py backend/tests/unit/test_adaptive_lanes.py backend/tests/unit/test_attempt_learning.py backend/tests/agent/test_router.py backend/tests/agent/test_recovery.py backend/tests/agent/test_quota_handling.py -q --tb=short` -> **104 passed in 7.55s**
  - `.venv/Scripts/python.exe -m pytest --ignore=backend/tests/fixtures -q --tb=short` -> **460 passed, 36 skipped in 29.32s**

## Remaining work
- Phase 4: Feature/refactor/config task types and import-graph V2 selective pytest.
- Phase 5: Live evaluation harness and end-to-end safety audit.

## Gotchas
- `ProviderHealth` circuit breaker state is strictly environmental/process-local and forbidden in checkpoints (`_FORBIDDEN_STATE_KEYS`).
- Ephemeral runs (`not explicit_persistence`) always clean up their local SQLite thread in a `finally` block so failed test runs never pollute subsequent runs.
- Fast-lane nodes always use `LOW` thinking and timeouts bounded by remaining fast sub-deadline; `route_specialist` raises `ValueError` if called with `lane="fast"`.

## Frontend design handoff
- Added `docs/GitResolve_Premium_UI_Implementation_Guide.md` for Antigravity, based on the existing brief, five visual references and current frontend source.
- Baseline brief preserved. Untracked guide preserved. Lenis mounts via shared provider; Motion and Lenis are already dependencies.
