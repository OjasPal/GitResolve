# Handoff — 2026-11-07 — Phase 4 Complete

## Branch and commits
- Branch: `agent/workflow-optimization`
- Prior commits: Phase 0 `21232ed`, Phase 1 `dfe0c8c`, speed `18c6da9`, Adaptive Lanes `9834328`, Phase 2A `9313144`, Phase 2B `42cb81f`, Phase 3 `6d02f65`, UI brief `9790052`, live recovery hardening `dd44dfd`.

## Working state and validation
- Completed **Phase 4**:
  - Bumped `LANES_POLICY_VERSION = "al.v2"`; in-flight `al.v1` checkpoints migrate to `FULL` lane + `RiskFloor.HIGH` on resume. Pinned `pinned_plan_hash` in `SqliteRepairCheckpointer` and `StoreRepairCheckpointer`.
  - Added deterministic `TaskType` (`bug | feature | refactor | config | unclear`) and monotonic `refine_task_type` in `triage.py`. Fast lane remains strictly bug-only; `unclear` routes to `needs_clarification` (0 attempts). Post-patch planner detects `feature_sized_diff_in_bug_mode` and stops for plan revision when an approved bug plan produces a feature-sized diff.
  - Added feature planning subgraph (`spec -> acceptance_criteria -> file_plan -> await_start`) in `agent/graph.py`, `compute_plan_hash`, `validate_plan_scope`, and `derive_limits(approved_plan, HARD_CAPS)` using `min()`.
  - Added mode-aware `validate_fix` (`policy.py`) and `validate_publication` (`publication_patch.py`) for `bug`, `feature`, `refactor` (pass-before/after + touched-symbol coverage + export stability), and `config` (`ALLOWED_CONFIG_PROFILES`), forcing `allow_static_oracle = False` for all non-bug modes.
  - Added default-deny `allowed_dependency_changes` in `requirements*.txt` (exact `package==version` + reason, wheel-only `prepare_environment` rebuild in `verify_node`, `FULL` lane + `V3`).
  - Added V2 conservative AST import-graph selective pytest (`import_graph.py`) for inner-loop `run_tests`, while final `verify_node` stays `V3` (full suite).
- Validation (0 live LLM tokens):
  - `.venv/Scripts/python.exe -m pytest backend/tests/unit/test_phase_4.py -q --tb=short` -> **28 passed in 3.14s**
  - `.venv/Scripts/python.exe -m pytest backend/tests/unit -q --tb=short` -> **310 passed in 26.66s**
  - `.venv/Scripts/python.exe -m pytest backend/tests/agent -q --tb=short` -> **30 passed in 0.63s**

## Remaining work
- Phase 5: Live evaluation harness, benchmark suite, and end-to-end safety audit.

## Gotchas
- `ProviderHealth` circuit breaker state is strictly process-local and forbidden in checkpoints (`_FORBIDDEN_STATE_KEYS`).
- Never use `.lstrip("./")` on relative paths that may be dotfile config profiles (`.flake8`, `.gitignore`, `.ruff.toml`); use `re.sub(r"^(?:\./)+", "", ...)`.
- Untracked `docs/GitResolve_Premium_UI_Implementation_Guide.md` is preserved from user's UI design work.
