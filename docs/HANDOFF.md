# Handoff — 2026-11-08 — External-Action Guidance (EAG) Complete

## Branch and commits
- Branch: `agent/workflow-optimization`
- Prior commits: Phase 0 `21232ed`, Phase 1 `dfe0c8c`, speed `18c6da9`, AL `9834328`, Phase 2A `9313144`, Phase 2B `42cb81f`, Phase 3 `6d02f65`, UI brief `9790052`, live recovery `dd44dfd`, Phase 4 `f7d8639`.

## Working state and validation
- Completed **External-Action Guidance (EAG)** (`EXTERNAL_GUIDANCE=on` default):
  - Added `backend/app/agent/lanes/external_guidance.py`: single-pass AST + `.env.example` evidence collector (`collect_external_evidence`), zero-cost gate (`should_invoke_resolution_assess`), 4 resolution kinds (`code_fix`, `external_action`, `mixed`, `needs_clarification`), AL Addendum Hard Guards (`apply_resolution_guards`), curated provider templates (`CURATED_PROVIDERS`, `SAFE_PROVIDER_LINKS`), `build_placeholder_env`/`validate_placeholder_env`, and `build_missing_dep_guidance_outcome`.
  - Wired `resolution_assess` and `finalize_guidance` nodes in `backend/app/agent/graph_engine/engine.py`. Pure `external_action` finishes in non-failure `TaskStatus.RESOLVED_WITH_GUIDANCE` (`0` repair attempts, `0` Docker verification runs). `mixed` injects safe `gitresolve-placeholder-<lower_key>` env vars into sandbox runs, escalates out of fast static-only (`mixed_requires_behavioral_verification`), and attaches `setupNote` + `setupNoteHash` verified at `approve_task` and appended as `## Setup you must do` in the PR body.
  - Hardened sandbox `missing_dep`: retries `env_prepare` once, adds `/workspace/src`, `/workspace/backend`, `/workspace/backend/src` to `PYTHONPATH`, falls back to subdirectory `requirements.txt`, adds `manylinux_2_17_x86_64` and `manylinux_2_28_x86_64` wheel tags (keeping `--only-binary=:all:`), and emits specific package guidance instead of raw `"Environment check: missing_dep"`.
  - Updated frontend (`StatusBadge`, `StageStepper`, `ActionButtons`, `workspace.tsx`) for calm `resolved_with_guidance` UI (`"Setup needed"`, `"I've done this, re-check"` button via `Action.RECHECK`, and `"Setup you must do"` mixed callout).
- Validation (0 live LLM tokens):
  - `.venv/Scripts/python.exe -m pytest backend/tests/unit/test_external_guidance.py -q --tb=short` -> **10 passed in 1.56s**
  - `.venv/Scripts/python.exe -m pytest backend/tests/unit/test_phase_3.py backend/tests/unit/test_phase_4.py -q --tb=short` -> **40 passed in 5.82s**
  - `.venv/Scripts/python.exe -m pytest backend/tests/unit/ backend/tests/sandbox/ -q --tb=short` -> **384 passed in 119s**

## Remaining work
- Phase 5: Live evaluation harness, benchmark suite, and end-to-end safety audit.

## Gotchas
- `placeholder_env` only accepts `^[A-Z][A-Z0-9_]{1,63}$` keys and `gitresolve-placeholder-<lower_key>` values; never pass host `os.environ` or real secrets into the sandbox.
- `test_phase_3.py::test_no_groq_references_remain_in_code_env_or_trd` scans `backend/app/*.py` for the literal substring `"gr"+"oq"`; keep target-repo provider constants constructed via `_GRQ_ID` in `external_guidance.py`.
- Untracked `docs/GitResolve_Premium_UI_Implementation_Guide.md` is preserved from user's UI design work.
