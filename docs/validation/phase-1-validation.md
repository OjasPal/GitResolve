# Phase 1 validation

Branch: agent/workflow-optimization. One local Phase 1 commit; no push or merge.

## Changes and evidence

- Static profiler: Python version declarations, frameworks, dependency/lock inventory, entrypoints, test directories/framework/pytest command, and referenced environment names. Five requested fixture profiles pass; non-Python is explicit unsupported.
- Wheel preparation: static requirement validation, fixed-index isolated pip wheel downloads with no dependency URL following, target-marker evaluation and metadata validation; offline venv installation in disposable Docker volumes; read-only runtime mounting. Cache keys include dependency file content, Python version and base image ID. Complete manifests and wheel hashes are checked before reuse.
- Baseline classifications separate dependencies/secrets/spec errors, collection failures, preexisting test failures, preparation failures and resource timeouts. Missing packages/secrets do not establish reproduction. Tests verify no repair-attempt charges for baseline infrastructure failure, missing dependencies after a proposal, or provider outages during evaluation.
- Streamlit prompts/configuration and review rules reside in its adapter; generic framework stubs leave existing pytest behavior intact. Existing AppTest gate regressions passed in the full suite.
- Context is a bounded tree plus ranked, numbered excerpts. A Windows short-path normalization regression found in the full run was fixed and its affected integration tests passed afterward.
- The five environment fixtures execute real Docker; prepared fixtures assert the venv prefix, read-only environment and denied network. Three failure fixtures cover missing dependencies/secrets and collection errors. Missing Docker yields explicit skips, never mocked integration success.
- Existing lifecycle, exact-diff approval, publication policy and sandbox isolation tests passed. No frontend changes.

## Commands and results

All commands run from the repository root with `$env:ENVIRONMENT='test'`.

```powershell
.venv/Scripts/python.exe -m pytest backend/tests/unit/test_repository_profile.py backend/tests/unit/test_dependencies.py backend/tests/unit/test_repair_runner.py -q --tb=short
```

48 passed. Earlier focused iterations exposed and corrected dependency outcome expectations and test-counter instrumentation.

```powershell
.venv/Scripts/python.exe -m pytest backend/tests/sandbox/test_prepared_environments.py -q --tb=short -ra
```

8 passed in 153.15 seconds on real Docker. Earlier runs exposed the missing host pip dependency and Windows bind-mount preparation overhead; pip is now declared/locked and prepared environments use disposable Docker volumes. Additional read-only/network assertions were subsequently exercised in the full run.

```powershell
.venv/Scripts/python.exe -m pytest -q --tb=short -ra
```

Full suite run once: 407 passed, 1 failed, 2 skipped in 123.91 seconds. Failure: Windows short-path versus resolved-path mismatch in planning context. Skips: Firestore dependency/emulator unavailable. All prepared-environment, Streamlit gate and other Docker tests passed.

```powershell
.venv/Scripts/python.exe -m pytest backend/tests/integration/test_github_docker_flow.py backend/tests/unit/test_repository_profile.py backend/tests/unit/test_repair_runner.py -q --tb=short -ra
```

After the Windows context fix and adapter review-rule extraction: 39 passed in 5.80 seconds. The full suite was not repeated.

`git diff --check` passed. Operational limitations and supported dependency formats are documented in `sandbox/README.md`. Wheel-only preparation deliberately reports unsupported requirements rather than running build scripts, installing sdists or fetching repository-selected URLs.
