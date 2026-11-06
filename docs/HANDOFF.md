# Handoff — 2026-11-06 — Adaptive Lanes (AL)

## Branch and commits
- Branch: `agent/workflow-optimization`
- Prior commits: Phase 0 `21232ed`, Phase 1 `dfe0c8c`, docs checkpoint `e6d9d4e`, speed `18c6da9`, docs consolidation `7aceb9b`, recoverable `blocked_reason` `6f1a4f5`.
- Current milestone commit: `feat: adaptive lanes`.

## Working state and validation
- Implemented `backend/app/agent/lanes/` (`types.py`, `triage.py`, `planner.py`, `oracle.py`, `escalation.py`) and wired into `run_repair()` behind `ADAPTIVE_LANES=on|off` (default `on` after shadow evaluation across trivial, PrompCraft-#2-shaped, medium, complex, and adversarial fixtures showed 0 unsafe-fast cases).
- Added trusted Docker `staticOracleReceipt` validation in `sandbox.py`, `policy.py`, `publication_patch.py`, and `worker_service.py` while keeping `testsRun > 0` mandatory for full-lane repairs.
- Full test suite: `.venv/Scripts/python.exe -m pytest -q --tb=short` -> **462 passed, 2 skipped** (Firestore) in 100.75s.
- Scripted real-Docker benchmark (`trivial` fixture, 0 live tokens): cold/pass-1 `3793.7 ms -> 2105.5 ms` (first action `1319.2 ms -> 418.7 ms`, Docker runs `3 -> 2`); warm/pass-2 `2541.1 ms -> 1739.8 ms` (first action `117.7 ms -> 18.1 ms`).

## Remaining work
- Milestone 2 (`feat: tool-using LangGraph repair engine`): implement Phase 2A `StateGraph` with shared AL modules, rehydratable overlay workspace, flat action envelope + 14-tool registry, SQLite checkpointer (`thread_id=f"{task_id}:{phase}"`), and `AGENT_ENGINE=graph|legacy` pinning.
- Deferred to Phase 2B+: Firestore checkpoint saver, reaper-driven resume, import-graph V2 test selection.

## Gotchas
- Keep `_ORIG_CHECKER` and `_PATCH_CHECKER` under 480 chars (`ExecutionRequest` enforces `<= 500` chars per command arg).
- Never pass baseline cache scope for patched verification; static-only fast lane requires verified Docker `staticOracleReceipt`.
