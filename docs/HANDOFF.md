# Handoff — 2026-11-07 — Phase 3 + Live Multi-Layer Repo & Recovery Hardening

## Branch and commits
- Branch: `agent/workflow-optimization`
- Prior commits: Phase 0 `21232ed`, Phase 1 `dfe0c8c`, speed `18c6da9`, Adaptive Lanes `9834328`, Phase 2A `9313144`, Phase 2B `42cb81f`, Phase 3 `6d02f65`, UI brief `9790052`.

## Working state and validation
- Implemented Phase 3 single-taxonomy failure classifier (`FailureCategory`, `classify_failure` in `agent/attempt_memory.py`), lane-aware strategy router edges, full-lane `specialist_reflect` (`gemini-4-argon`, capped at 2/task across resume), per-model `ProviderHealth` circuit breakers, Google-only routing, and optional `GITRESOLVE_BEST_OF_N`.
- Hardened live execution after `OjasPal/PromptCraft` webhook issue (`tsk_e652ba14a0776c4f0d23`) post-Phase-3 regression:
  - Wrapped `patch_node` context-recovery `_invoke_router_normal` (`engine.py`) and `Worker.process` (`runner.py`) in `(ProviderError, AllProvidersFailedError)` handlers returning `infra_retry` (`FailureCode.LLM_UNAVAILABLE`) so provider timeouts never fall through to `public_error(exc)` (`Reference: <hex>`).
  - Capped SDK `ThinkingConfig(thinking_level="LOW")` in `GoogleGenAIProvider.generate` when `schema is not None and len(str(prompt)) > 12000` to prevent Vertex AI constrained JSON decoding from stalling into 58s `504 DEADLINE_EXCEEDED` timeouts on large repo prompts.
  - Updated `EditProposal.actionable_or_blocked`, `repository_context` (including `.ts`/`.tsx` and full files `<= 100` lines), `_referenced_files`, and `_expand_context_for_files` so multi-layer repos solve cleanly on Call 1 (`8.49s` on `OjasPal/PromptCraft`).
- Validation:
  - `.venv/Scripts/python.exe -m pytest backend/tests/unit/test_phase_3.py backend/tests/unit/test_repair_runner.py backend/tests/unit/test_phase_2a_graph.py backend/tests/unit/test_phase_2b_recovery.py backend/tests/unit/test_adaptive_lanes.py backend/tests/unit/test_attempt_learning.py backend/tests/agent/test_router.py backend/tests/agent/test_recovery.py backend/tests/agent/test_quota_handling.py -q --tb=short` -> **116 passed in 10.53s**

## Remaining work
- Phase 4: Feature/refactor/config task types and import-graph V2 selective pytest.
- Phase 5: Live evaluation harness and end-to-end safety audit.

## Gotchas
- `ProviderHealth` circuit breaker state is strictly environmental/process-local and forbidden in checkpoints (`_FORBIDDEN_STATE_KEYS`).
- Large structured-output prompts (`schema` + `>12KB`) must use `LOW` SDK thinking on Vertex AI to avoid 60s constrained-decoding `504` timeouts.

## Frontend design handoff
- Added `docs/GitResolve_Premium_UI_Implementation_Guide.md` for Antigravity, based on the existing brief, five visual references and current frontend source.
- Baseline brief preserved. Untracked guide preserved. Lenis mounts via shared provider; Motion and Lenis are already dependencies.
