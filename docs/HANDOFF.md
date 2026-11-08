# Handoff — 2026-11-08 — External-Action Guidance (EAG) & NexusMind Reliability Fixes

## Branch and commits
- Branch: `agent/workflow-optimization`
- Prior commits: Phase 0 `21232ed`, Phase 1 `dfe0c8c`, speed `18c6da9`, AL `9834328`, Phase 2A `9313144`, Phase 2B `42cb81f`, Phase 3 `6d02f65`, UI brief `9790052`, live recovery `dd44dfd`, Phase 4 `f7d8639`, EAG `e6d925b`, EAG live fixes `b8db4df`, `21bc798`.

## Working state and validation
- Fixed `An unexpected error interrupted this operation. Reference: ...` and NexusMind `GROQ_API_KEY` end-to-end reliability:
  - Fixed `outcome_for()` (`backend/app/services/outcomes.py`) to map `llm_unavailable -> infra_retry` instead of `cannot_fix_with_report`, eliminating the `LeaseLost` transaction crash in `WorkerService.fail()` during `infra_retry`.
  - Added Guard 4 in `apply_resolution_guards()` (`backend/app/agent/lanes/external_guidance.py`) so when the LLM classifies an issue as `code_fix` despite explicitly naming a verified credential env var (`GROQ_API_KEY`), it deterministically promotes `code_fix -> mixed` and attaches the clean plain-URL setup steps (`https://console.groq.com/keys`, no markdown brackets).
  - Auto-pinned `bcrypt==4.0.1` in `declared_requirements()` (`sandbox/dependencies.py`) when `passlib` is declared without a `bcrypt` pin, and updated `verify_node()` (`backend/app/agent/graph_engine/engine.py`) to accept patched verification when all target/regression tests pass and the only failing test files were already failing at baseline.
  - Added `_mixed_guidance_fallback()` in `engine.py`, fallback model continuation in `agent/router.py`, and two-tier checkpoint compaction in `StoreRepairCheckpointer._persist_thread()` (`checkpointer.py`).
- Validation (0 live LLM tokens):
  - `.venv/Scripts/python.exe -m pytest backend/tests/unit/test_external_guidance.py backend/tests/unit/test_phase_3.py backend/tests/unit/test_phase_4.py -q --tb=short` -> **53 passed in 9.84s**
  - `.venv/Scripts/python.exe -m pytest backend/tests/unit/test_external_guidance.py backend/tests/unit/test_phase_3.py backend/tests/unit/test_phase_4.py backend/tests/agent/test_recovery.py backend/tests/unit/test_repair_runner.py -q --tb=short` -> **93 passed in 17.19s**

## Remaining work
- Phase 5: Live evaluation harness, benchmark suite, and end-to-end safety audit.

## Gotchas
- `build_placeholder_env` only populates credential env vars (`_is_credential_env_name`) and skips non-string `BaseSettings` fields (`_annotation_allows_string`).
- Untracked `docs/GitResolve_Premium_UI_Implementation_Guide.md` is preserved from user's UI design work.
