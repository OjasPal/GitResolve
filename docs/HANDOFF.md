# Handoff — 2026-11-10 — Firestore Read-After-Write Fix & Error Sanitization

## Branch and commits
- Branch: `agent/workflow-optimization`
- Recent commit: `e35958f` fix(worker): prevent Firestore read-after-write in complete_execution and sanitize UI error references

## Working state and validation
- Root cause:
  - In `WorkerService.complete_execution` and `complete_guidance`, `txn.get(cp_path)` was called after `txn.set(artifact_path(...))`. In Google Cloud Firestore transactions, all reads must precede writes, which triggered `ReadAfterWriteError` when transitioning verified repairs to `awaiting_approval`.
- Fixes applied:
  - `backend/app/services/worker_service.py`: Read checkpoint upfront (`txn.get(cp_path)`) before staging artifact writes in `complete_execution` and `complete_guidance`.
  - `backend/app/services/store/memory.py`: Added strict read-after-write guard to `_MemTxn` so unit tests catch Firestore read-after-write regressions locally.
  - `frontend/src/components/EventTimeline.tsx`: Scrubbed raw hex incident references (`Reference: <hex>`) and replaced unexpected internal error text with user-friendly notices.
  - `backend/tests/unit/test_workflow_optimization.py`: Added `test_transaction_read_after_write_prevented` verifying `_MemTxn` rejection and `complete_execution`/`complete_guidance` read ordering.
- Validation:
  - `test_workflow_optimization.py`: 30 passed in 1.91s.
  - `test_repair_runner.py`, `test_phase_2b_recovery.py`: 70 passed in 8.97s.
  - Frontend production build: verified clean (0 errors).
  - Deployed updated `worker_service.py` to GCE VM `gitresolve-worker` and restarted `gitresolve-worker.service` (active and polling).
  - Redeployed Cloud Run API revision `gitresolve-api-00004-bk6` (healthy).

## Remaining work
- Push `agent/workflow-optimization` / merge to `main` upon user authorization to trigger Vercel frontend update.

## Gotchas
- Firestore transactions strictly enforce read-before-write across all documents in the transaction callback.
