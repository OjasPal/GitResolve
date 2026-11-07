# Handoff — 2026-11-08 — External-Action Guidance (EAG) & NexusMind Fix Complete

## Branch and commits
- Branch: `agent/workflow-optimization`
- Prior commits: Phase 0 `21232ed`, Phase 1 `dfe0c8c`, speed `18c6da9`, AL `9834328`, Phase 2A `9313144`, Phase 2B `42cb81f`, Phase 3 `6d02f65`, UI brief `9790052`, live recovery `dd44dfd`, Phase 4 `f7d8639`, EAG `e6d925b`, EAG live fix `55f0789`.

## Working state and validation
- Completed **External-Action Guidance (EAG)** (`EXTERNAL_GUIDANCE=on` default) + live NexusMind `BaseSettings` fix (`55f0789`):
  - Restricted `BaseSettings` evidence extraction and `build_placeholder_env` (`backend/app/agent/lanes/external_guidance.py`) to string credential fields (`_is_credential_env_name` + `_annotation_allows_string`), preventing string placeholders (`gitresolve-placeholder-...`) from overwriting `int`/`float`/`bool` fields like `ACCESS_TOKEN_EXPIRE_MINUTES: int` or `DEFAULT_CHUNK_SIZE: int` during pytest collection.
  - Persisted the `mixed` `"Setup you must do"` guidance note (`finalDiffSummary.setupNote` and `whatWeFound`) immediately upon `resolution_assess` completion (`engine.py` + `PROGRESS_FIELDS` in `worker_service.py`) so the user sees the external setup instructions in the workspace right away as well as at PR approval.
  - Pure `external_action` finishes in non-failure `TaskStatus.RESOLVED_WITH_GUIDANCE` (`0` repair attempts, `0` Docker verification runs) with `Action.RECHECK` (`"I've done this, re-check"`).
- Validation (0 live LLM tokens):
  - `.venv/Scripts/python.exe -m pytest backend/tests/unit/test_external_guidance.py backend/tests/unit/test_phase_3.py -q --tb=short` -> **23 passed in 6.34s**
  - `.venv/Scripts/python.exe -m pytest backend/tests/unit/ backend/tests/sandbox/ -q --tb=short` -> **384 passed in 119.5s**

## Remaining work
- Phase 5: Live evaluation harness, benchmark suite, and end-to-end safety audit.

## Gotchas
- `build_placeholder_env` only populates credential env vars (`_is_credential_env_name`) and skips non-string `BaseSettings` fields (`_annotation_allows_string`) so `pydantic_settings` integer/boolean fields never fail validation.
- Untracked `docs/GitResolve_Premium_UI_Implementation_Guide.md` is preserved from user's UI design work.
