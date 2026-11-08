# Handoff — 2026-11-09 — Fix Context Recovery Starvation, Event Path Leak, and NoneType Evidence Crash

## Branch and commits
- Branch: `agent/workflow-optimization` (merged with `main`).
- Included milestones: Phase 0 `21232ed`, Phase 1 `dfe0c8c`, speed `18c6da9`, AL `9834328`, Phase 2A `9313144`, Phase 2B `42cb81f`, Phase 3 `6d02f65`, Phase 4 `f7d8639`, EAG `e6d925b`–`7727b08`, and context/evidence fixes.

## Working state and validation
- Resolved `Reference: 57fa72adadc840d8b54515db320e237a` crash and `patch` context-recovery issues:
  - Fixed `_serialize_execute_result` / `_deserialize_execute_result` (`backend/app/agent/graph_engine/engine.py`), `ExecuteResult` / `PlanResult` (`backend/app/agent/interface.py`), and `WorkerService.fail` (`backend/app/services/worker_service.py`) so empty `evidence=[]` never deserializes as `None` or raises `TypeError: 'NoneType' object is not iterable`.
  - Fixed `_expand_context_for_files` (`backend/app/agent/repair_runner.py`) to check line completeness within each file's own section rather than across the entire `base_context`, and prioritized `.py` focus files in `repository_context` so frontend `.tsx`/`.ts` files cannot starve backend Python files.
  - Replaced raw file path list in the `patch` context-recovery event message with `"Expanding repository context to inspect additional source sections."` and prioritized explicitly requested `needed_files` + specialist escalation on recovery.
- Validation (0 live LLM tokens):
  - `.venv/Scripts/python.exe -m pytest backend/tests/unit/test_repair_runner.py backend/tests/unit/test_external_guidance.py backend/tests/unit/test_workflow_optimization.py -q --tb=short` -> passed.

## Remaining work
- Phase 5: Live evaluation harness, benchmark suite, and end-to-end safety audit (deferred for future).

## Gotchas
- `load_repository_originals` excludes `node_modules`, `.venv`, `venv`, `__pycache__`, `.git`, and `.tox`.
- `redaction.py` secret assignment regex uses horizontal whitespace (`[ \t]*[:=][ \t]*`) so multiline code diffs are never corrupted.
