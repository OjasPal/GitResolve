# GitResolve agent instructions

GitResolve investigates Python repository issues, proposes verified repairs, and creates a GitHub PR only after human approval. Prefer low-cost execution and the existing Google GenAI ModelRouter.

## Session continuity
- At session start, read `docs/ROADMAP.md` and the latest dated `docs/handoffs/*.md`, then inspect git status. Repository state is authoritative.
- At session end, write a new dated handoff with branch, working state, validation, remaining work, and gotchas. Never include credentials or private task contents.
- Work on `agent/workflow-optimization` unless instructed otherwise. Preserve existing changes; never discard or stash them. Commit per approved milestone. Never push or merge without explicit authorization.

## Non-negotiable safety
- Execute untrusted repository/package code only in hardened Docker containers. No host shell tools for repository code.
- Tests run with network disabled; no secrets or Docker socket in the sandbox.
- Require human approval of the exact diff hash before any target-repository branch, commit, or PR. Never auto-merge.
- Keep deterministic policy/patch checks independent of the model, regression-before/after evidence, full-suite verification, and independent review.
- Agents return AgentRunner results; only backend services change task statuses. Human gates remain backend states, not graph interrupts. Check leases/cancellation between steps.

## Layout
- `backend/app/agent/repair_runner.py`: current `run_repair()` execution loop; graph replacement is not implemented yet. `frameworks.py`: framework prompt/verification adapters.
- `agent/graph.py`: planning graph, not the repair execution graph. `agent/router.py`: provider routing, fallback, specialist escalation, and health handling. Use this router for every provider call.
- `backend/app/worker/`: execution orchestration and sandbox bridge; `backend/app/services/`: lifecycle, approval, storage, and GitHub boundary.
- `sandbox/`: static profiling, wheel preparation, hardened execution, and framework gates. Read its README before changing execution.
- `githubapp/`: GitHub authentication/client; `frontend/`: app UI.

## Validation
From the repository root in PowerShell:
```powershell
$env:ENVIRONMENT='test'
.venv/Scripts/python.exe -m pytest backend/tests/unit/test_repair_runner.py -q --tb=short
.venv/Scripts/python.exe -m pytest backend/tests/sandbox/test_prepared_environments.py -q -ra
```
Choose targeted tests for the change. Run the full suite rarely, at milestone completion, not repeatedly during edits. Real Docker tests require the reviewed images; report skips honestly. Fixture repositories contain intentional failures and must not be collected as host tests. Record exact commands/results and actual model token usage when applicable.
