# Handoff — 2026-11-06 — speed and STOP A

## Branch and commits

Branch: `agent/workflow-optimization`. Prior commits: Phase 0 `21232ed`, Phase 1
`dfe0c8c`, documentation checkpoint `e6d9d4e`. This milestone's commit message is
exactly `speed`; obtain its hash with `git log -1 --format=%h -- docs/speed-validation.md`.
The handoff is included in that commit, so it cannot contain its own hash.
No push or merge. The working tree should be clean after this milestone commit.

## Implemented and verified

See `docs/speed-validation.md` for exact commands and raw reports. Scoped TTL,
ETag and token caching; lazy pages; bounded permission calls; fresh publication
authorization; prepared environments; original-only baseline cache; cancellable
plan-review prewarm; nonblocking frontend and shared per-session query cache.
Timing covers SDK invocation, stage completion, known tokens and actual Docker
launches; prewarm is separately attributed.

Full suite once: **428 passed, 2 skipped** in 107.82 s. Both skips are Firestore.
Final small gateway error-contract correction and new test: **43 affected tests
passed**, no full rerun. Real cache/prepared-environment targeted run: **24 passed**.
Frontend build, lint and **5** cache/loading tests passed. Runtime source and
publication gates remain in place; no live target repo was changed.

Live listings before repeat/after repeat: repos 19.296 s/0.345 ms; issues
20.535 s/0.062 ms. Cold after repo listing 11.433 s. These are gateway process
timings. Final scripted cache-off/on warm repair: trivial 4.497/3.305 s and 3/2
Docker launches; multi-file behavior 16.502/4.296 s and 6/2 launches. Zero live
tokens in scripted fixtures. First scripted proposal: 1.569/0.226 s and
5.502/0.611 s. These do not establish Start-to-real-model latency.

Schema probe: discriminated union rejected locally by SDK; flat enum/args
succeeded through ModelRouter with **199 input / 55 output tokens** in 15.426 s.
Use flat actions plus per-tool validation; do not repeat live probes unnecessarily.

## STOP A and next work

Present one user-facing STOP A message with timing tables, AL proposal, graph
summary and probe outcome, then wait for approval. The user's continuation after
usage limits did not explicitly waive this gate. AL and graph remain unimplemented.

After approval, do `AL`, then `2A-graph`, without further planned stops. AL lives
in graph-agnostic typed pure modules and is first wired to legacy run_repair.
Graph nodes must call the same modules. Caches/prewarm belong to speed. Fast gets
its own tight budget and no explore loop; full/escalation use tools. Default lanes
on only with zero unsafe-fast shadow-eval cases. SQLite only; Firestore/reaper
resume is 2B. Preserve all existing safety and backend-owned human gates.

## Limitations and gotchas

- No selected-identity historical PrompCraft #2 record; original issue shape,
  plan duration, human wait and historical per-stage tokens cannot be claimed.
- Browser inspection approval timed out. Browser navigation/hydration/dropdown
  timing and visual QA unverified. HTTP route measurements are separate.
- Saved shared backend tunnel and local port 8010 unavailable. Local 8000 health
  returned 200; no successful tunnel-vs-local comparison. No tunnel restarted.
- A temporary production server used port 3103 for final HTTP measurements; the
  existing user's development server uses 3000. Stop only task-owned temporary
  servers, never unrelated user servers.
- Environment cache volumes are read-only at runtime, bounded to 8 per process,
  TTL 30 minutes; normal shutdown clears idle entries. Abrupt termination may
  leave volumes requiring operator cleanup after verifying no worker uses them.
- Prewarm cap is per process (one job, 60 s); it does not reuse a runtime container
  or execute edits, and it cancels on task/gate changes. Baseline TTL is five minutes.
- Synthetic fixture newline failure and shutdown logging warning were fixed;
  final measurements and full-suite results are not the earlier failed runs.
- Static-only AL success needs a narrow backend-validated proof receipt. Do not
  just remove the existing publication requirement for passing Docker tests.
- Current model SDK rejects union schema; graph tools need flat envelope validation.

Original prompts remain verbatim in `docs/prompts/AL.md` and `PHASE-2A.md`.
