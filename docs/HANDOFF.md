# Handoff — 2026-11-06 — Phase 2A Tool-Using LangGraph Repair Engine

## Branch and commits
- Branch: `agent/workflow-optimization`
- Prior commits: Phase 0 `21232ed`, Phase 1 `dfe0c8c`, docs checkpoint `e6d9d4e`, speed `18c6da9`, docs consolidation `7aceb9b`, recoverable `blocked_reason` `6f1a4f5`, adaptive lanes `9834328`.
- Current milestone commit: `feat: tool-using LangGraph repair engine`.

## Working state and validation
- Implemented `backend/app/agent/graph_engine/` (`state.py`, `checkpointer.py`, `workspace.py`, `tools.py`, `engine.py`) and wired `run_repair()` behind `AGENT_ENGINE=graph|legacy` (default `graph`, `legacy` preserved alongside `agent/repair.py`).
- `StateGraph(RepairState)` integrates shared `backend/app/agent/lanes/` (`ADAPTIVE_LANES=on` default), `SqliteRepairCheckpointer` (`thread_id=f"{task_id}:{phase}"`, RLock-synchronized, schema `2a.v1` and engine pinning), rehydratable `WorkspaceOverlay`, and 14 typed tools using the Gemini-validated flat `RepairActionEnvelope`.
- Targeted tests: `backend/tests/unit/test_phase_2a_graph.py` -> **11 passed in 1.88s**; real Docker/Streamlit `test_prepared_environments.py` + `test_streamlit_gate.py` -> **22 passed in 56.22s**.
- Scripted real-Docker benchmark (`trivial`, 0 live tokens): cold/pass-1 `3793.7 ms -> 2105.5 ms` (first action `1319.2 ms -> 418.7 ms`, Docker runs `3 -> 2`); warm/pass-2 `2541.1 ms -> 1739.8 ms` (first action `117.7 ms -> 18.1 ms`).
- Full test suite: `.venv/Scripts/python.exe -m pytest -q --tb=short` -> **473 passed, 2 skipped** (Firestore).

## Remaining work
- Phase 2B: Firestore checkpoint saver (`FirestoreSaver`) and reaper-driven graph resume across worker hosts.
- Phase 3+: Strategy router with per-model circuit breakers, import-graph V2 selective pytest, feature/refactor task types, and live evaluation harness.

## Gotchas
- `SqliteRepairCheckpointer` must hold `self._lock` (`RLock`) across `put`/`put_writes`/`get_tuple` because LangGraph's `SyncPregelLoop` dispatches checkpoint writes via `BackgroundExecutor` threads.
- Keep Docker `-c` checker scripts `<= 480` chars (`ExecutionRequest` enforces `<= 500` chars per command arg).
- Both human approval gates (`awaiting_start`, `awaiting_approval`) remain backend states in `WorkerService`; never use `langgraph.types.interrupt()`.
