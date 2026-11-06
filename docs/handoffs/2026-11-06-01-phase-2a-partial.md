# Handoff - 2026-11-06 - Phase 2A partial

## Branch and working state

- `agent/workflow-optimization`; HEAD before documentation: `dfe0c8c` (Phase 1), preceded by `21232ed` (Phase 0).
- Only root `AGENTS.md`, `docs/ROADMAP.md`, and this handoff belong to the documentation commit. Find its hash with `git log -1 -- docs/ROADMAP.md`; a commit cannot contain its own hash.
- Preserved modified code: `backend/app/services/github_real.py`, `backend/app/worker/context.py`, `githubapp/auth.py`, `githubapp/client.py`.
- Preserved untracked code: `githubapp/timing.py`, `scripts/benchmark_github_listings.py`.
- No push/merge; no Phase 2A milestone commit. Do not discard or stash pending work.

## Next steps

1. Read the roadmap and inspect status. The latest user instruction forbids code changes for this checkpoint; obtain renewed authorization before continuing implementation.
2. Resume speed FIRST. Fix the benchmark to select the intended local identity explicitly and derive its ID from `users/<id>` keys. Two users exist; do not benchmark another account or print local state. OjasPal is the project owner identified in this session.
3. Measure listing latency and small-fix stage/Docker-count baseline before optimization. Initial benchmark exited before requests: no baseline exists yet.
4. Implement first-page/lazy listing, scoped token/TTL/ETag caches, bounded concurrency and nonblocking shell; validate authorization isolation/revocation and UI loading/errors. Report before/after timings and commit speed milestone before graph work.
5. Probe AgentAction using one real Gemini call through ModelRouter; report actual token usage. Then implement the approved SQLite-only graph and its tests. Firestore saver/reaper-resume are Phase 2B.
6. Commit per milestone. Update roadmap and write a fresh handoff at session end.

## Validation

Instrumentation: 9 passed using `ENVIRONMENT=test` and `backend/tests/unit/test_worker_recovery.py backend/tests/unit/test_core.py`. No Phase 2A live timing, schema-probe, token-usage, graph-test or full-suite result exists yet. Phase 0/1 results and exact commands are linked in the roadmap. Documentation-only checkpoint needs diff/content checks, not another suite.

## Gotchas

- `ENVIRONMENT=test` prevents app import from opening live state. A second MemoryStore owner on the same state file is intentionally rejected.
- Windows FastAPI tests may require local socket access; restricted runs previously hung creating socket pairs.
- Docker integration requires reviewed images. Report skips honestly. Prepared venvs use disposable Docker volumes; Windows bind-mounted venvs were too slow.
- Normalize Windows short/long paths before relative comparisons.
- `agent/graph.py` is planning only; `run_repair()` is still a Python loop. Prototype `agent/repair.py` has test references.
- Instrumentation is preliminary: verify first-model timing represents an actual call, final stages flush, and nested/background timing is attributed correctly before publishing measurements.
- Caches cannot replace authoritative publication authorization. Token refresh must not repeatedly return a rejected cached token.
- Never put credentials/raw local state into docs, logs, checkpoints or sandbox environments.
