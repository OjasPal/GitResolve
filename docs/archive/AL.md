PROJECT: GitResolve. TASK NAME: ADAPTIVE LANES (AL).

START
Read AGENTS.md, docs/ROADMAP.md and the latest docs/handoffs/*.md first (if missing, tell me and stop). Confirm branch and git status. Never touch uncommitted work from another phase.
Be limit-aware: make small commits at clean boundaries, run targeted tests (not the full suite) while iterating, run the full suite at most once per milestone, and write docs/handoffs/adaptive-lanes.md before stopping for any reason.

PROBLEM
run_repair() in backend/app/agent/repair_runner.py runs the same pipeline for every issue: profile and read all files, full baseline suite in Docker, LLM proposal including a mandatory new regression test, reproduction run in Docker, full patched suite in Docker, LLM review, policy gate, human approval. PrompCraft issue #2 (one added line, one removed line) took ~8 minutes. Goal: an adaptive workflow that is fast for low-risk localized fixes and unchanged in rigor for everything else.

CODEBASE FACTS (verify, they may have changed)
- agent/graph.py is only the planning graph. The repair pipeline is the imperative run_repair(); integrate there, not into a LangGraph.
- agent/router.py is the MODEL failover router. Do not reuse that name. Put new code in a clearly named module (e.g. agent/lanes/ or backend/app/agent/lanes.py).
- Phase 1 already provides: sandbox/profiler.py, wheel-only prepared environments with cache keys (sandbox/environment.py, dependencies.py), baseline classification (sandbox/classification.py), AttemptMemory, validate_proposal, publication_patch (strips new tests from the PR patch). Reuse them. Do not rebuild them.
- Existing signals worth reusing but not trusting: AgentState.is_complex_issue, PatchProposal.cross_file_impact (model-supplied).

STEP 1 - MEASURE (read-only)
Using existing stage events (SETUP, BASELINE, LOCATE, PATCH, REPRODUCE, TEST, EVALUATE, POLICY_GATE), event timestamps and token usage, produce a per-stage time and token breakdown for (a) the PrompCraft #2 run if its data exists locally, and (b) a trivial and a complex fixture. Include cold vs warm environment cache. Also measure the pre-run plan stage and the human-approval wait. Report where time actually goes. Add missing timing instrumentation if needed (small commit).

STEP 2 - PROPOSAL (document, then STOP for my approval)
Design these, grounded in the Step 1 numbers:

A. Two decision points inside run_repair.
 1. Pre-patch triage after profile_repository: deterministic, no LLM, no repo-code execution, ~seconds. Produces a typed TriageReport (candidate lane, risk floor, localized files, signals).
 2. Post-patch planner after the proposal is compiled: deterministic, computed from the ACTUAL diff (AST-level change classification, symbol reference and side-effect check for removals, path policy, dependency changes). It picks the verification level and final lane.
- Issue text is untrusted/adversarial: it may raise risk, never lower it. The LLM may escalate, never downgrade below the deterministic floor. No LLM self-reported confidence as a gate. No line-count-only classifier.

B. Lanes and verification levels.
 - Fast lane eligibility (ALL must hold): allowlisted change classes (add import of stdlib or an already-declared dependency, remove an unused import, comment/docstring/string typo, maybe unambiguous undefined-name fixes), no risk overrides, localized, AND a cheap oracle can show failure on the ORIGINAL code.
 - Full lane: everything else, including behavior/logic bugs with no oracle. Rule: no failing-original evidence, no fast lane. The full lane must behave exactly like today's pipeline.
 - Levels: V0 static oracle (compile/AST/undefined-name or lint delta, original vs patched, run INSIDE the sandbox, preferably with our own small checker so the sandbox image needs no new tooling); V1 import/smoke in a fresh network-off container; V2 targeted tests (import-graph selection plus any existing tests of the touched modules); V3 full suite (= full lane).
 - Fast-lane proposal does not require an LLM-authored regression test; V0 delta is the reproduction evidence. Make verification and outcome text TRUTHFUL (e.g. "static verification passed" must never read as "tests passed"); extend summary/verification fields with lane, verificationLevel, oracle, and timing without breaking the existing API contract.
 - Hard overrides to full lane: auth/security/crypto/serialization/subprocess/eval/SQL code, CI/Docker/dependency manifests/lockfiles, migrations, public API or __init__ exports, test or conftest edits, removals whose symbols are still referenced or have side effects, new third-party dependencies, secrets-like content.
 - Fast-lane review: the independent QualityReview call stays, with a minimal prompt (issue, diff, oracle evidence) instead of the full repo context; it may run concurrently with V1/V2.

C. Monotonic escalation. fast -> full only, never back. Triggers: scope growth, oracle disagreement, failed verification, risky path discovered, hidden dependency, localization uncertainty, flaky results. Carry the collected evidence (do not redo finished work), cap total time/model-call budget, record every escalation reason as a task event.

D. Latency optimizations (keep only what Step 1 justifies): run the baseline concurrently with LLM proposal generation (verify thread-safety of ctx.execute/artifact/usage/checkpoint before doing so); speculative profile + environment prep + baseline during the human plan-approval wait (with a resource cap and cancellation if the plan is rejected); lazy file reading instead of loading the whole repo; smaller localized context for LLM calls; targeted-first test selection; skip redundant baseline work for fast lane while keeping the cheap infra-failure detection; prompt caching; smaller model for trivial proposals. Never remove the human plan-approval or diff-approval gate.

E. Invariants that must be TESTED, not just documented: repo code and tooling over repo content run only in Docker; fresh container per task (cache images/layers/venvs, never reuse containers); dependency preparation stays wheel-only; no secrets in sandbox; test execution network-off; deterministic policy validation (validate_proposal, limits) unchanged; attempt budgets and AttemptMemory unchanged; exact diff-hash human approval before any branch/commit/PR; never auto-merge.

F. Include: integration points with file/function names, state and schema changes, failure-mode table, estimated latency per lane (labelled as estimates; realistic target ~20-40s p50 for trivial issues, not human-speed), and anything that conflicts with an existing contract.

STEP 3 - EVAL HARNESS (in the proposal)
Labeled fixtures: trivial, medium, complex and ADVERSARIAL (issue text claiming "trivial, just remove the check", removals with side effects, new dependencies, fake oracle passes). Use deterministic drivers like the existing test_github_docker_flow, not live LLMs, for tests. Metrics: p50/p95 latency, model calls/tokens, Docker runs, escalation rate, false-fix rate, unsafe-fast-lane rate (fast lane approved but the full lane would reject: run the full lane in shadow on the fixtures). Success = lower latency and resource use with no increase in false fixes, false PRs or security regressions.

STEP 4 - IMPLEMENT (only after I approve)
Small commits in this order, tests alongside each: (1) instrumentation, (2) TriageReport, (3) post-patch planner + overrides + allowlist, (4) V0/V1 oracles wired into run_repair, (5) escalation with evidence handoff, (6) justified optimizations, (7) eval harness + before/after benchmark report. Required tests: each override forces the full lane; "trivial" claims in issue text cannot lower risk; behavior bugs without an oracle go to the full lane; escalation is monotonic and carries evidence; invariants in E; the PrompCraft #2 shape takes the fast lane; the full lane output is byte-for-byte unchanged on existing fixtures. All existing lifecycle, approval, publication and sandbox tests must still pass.

DELIVERABLES: measured profile, proposal (then stop), implementation with tests, benchmark report, docs/handoffs/adaptive-lanes.md, and an update to docs/ROADMAP.md.