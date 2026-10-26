# GitResolve: Product Requirements Document (PRD)

**Version:** 1.0 | **Status:** Draft for review | **Doc 1 of 6** (PRD → TRD → App Flow → UI/UX Brief → Backend Schema → Implementation Plan)

> This file is the source of truth for what we are building. It is written to be read by both humans and AI coding tools. Requirement IDs (F-xx, US-xx, M-xx) are stable references for later documents and prompts.

---

## 1. App Name & Value Proposition

**Name:** GitResolve

**Tagline:** Autonomous AI engineer that turns GitHub issues into tested pull requests.

**Value proposition:** GitResolve takes a GitHub issue, investigates the codebase, reproduces the bug, writes a fix, runs the tests, and opens a pull request, with a full evidence trail and a human approving every step that touches the repository.

**Guiding principle:** *Autonomous execution, verifiable results, human-controlled integration.*

GitResolve does not just suggest code. It works in an isolated environment, validates its own changes against real tests, and never claims success because the LLM says so. Success means the tests pass.

---

## 2. Core Problem & Target Audience

### 2.1 Problem

Developers lose a large share of their time on repetitive debugging work:

- Understanding an unfamiliar codebase and finding the relevant code
- Reproducing bugs from incomplete issue descriptions
- Writing regression tests and identifying the right fix
- Interpreting test failures and iterating
- Writing PRs with explanations and test evidence

Existing AI assistants generate plausible code, but a plausible patch is not a completed engineering task. The gap is the **whole workflow**: from issue to a validated, reviewable change.

### 2.2 Solution (workflow)

| # | Stage | What happens |
|---|-------|--------------|
| 1 | Receive | Fetch the GitHub issue and repo context |
| 2 | Investigate | Inspect code, dependencies, relevant tests |
| 3 | Reproduce | Write and run a failing test or reproduction |
| 4 | Repair | Generate a patch, run tests, retry on failure (bounded) |
| 5 | Verify | Check test results, regressions, scope limits |
| 6 | Submit | After human approval, open a PR |

If a stage fails, the agent may retry, revise its plan, ask for human input, or stop with a clear failure report.

### 2.3 Target audience

- **Primary:** Individual developers working on their own Python repositories who want to delegate well-defined bug fixes and review the result.
- **Secondary (later):** Small teams and open-source maintainers wanting faster first-pass fixes and consistent validation.

### 2.4 Out of scope for V1

- Auto-merging or deploying code
- Unrestricted execution on arbitrary machines
- Guaranteed resolution of every issue
- Enterprise admin, compliance, or multi-tenant governance
- Safety-critical software maintenance

---

## 3. Product Assumptions (decisions made, change if wrong)

| Decision | Assumed for V1 |
|----------|----------------|
| Target user | Individual developers on their own repos |
| Languages | Python only |
| Source control | GitHub only |
| Agent orchestration | LangGraph (stateful agent workflow) |
| Autonomy level | Human reviews the diff and approves before the PR is created |
| Merging | Never automatic; humans review and merge |
| Supported repos | Controlled set of repo types and test commands (pytest) |
| Evaluation | Fixed benchmark with recorded configurations |

---

## 4. Essential Core Features (MVP) vs. Future Scope (V2)

**Priority:** P0 = required for launch, P1 = important improvement, P2 = future.

### 4.1 MVP (V1)

| ID | Feature | Key requirements |
|----|---------|------------------|
| F-01 | **GitHub integration** (P0) | GitHub sign-in; select an authorized repo and issue; fetch issue text, labels, comments; least-privilege permissions; revocable access |
| F-02 | **Issue analysis & planning** (P0) | Convert the issue into a structured problem statement; identify likely files and tests; produce a bounded plan with completion criteria; ask for clarification or stop if the issue is too ambiguous |
| F-03 | **Repo exploration & bug reproduction** (P0) | Clone into an isolated workspace; search code; run approved setup/test commands; record baseline results; mark reproduction as succeeded, failed, or inconclusive |
| F-04 | **Patch generation & self-correction** (P0) | Generate a focused patch; run tests and lint; feed structured failures back into the agent; configurable max repair attempts; stop when evidence is insufficient |
| F-05 | **Verification & PR creation** (P0) | Enforce diff-size and scope limits; require tests to pass; separate pre-existing failures from new ones; create a branch and open a PR with summary, test results, and known limitations; **explicit human approval required before PR creation** |
| F-06 | **Execution timeline & audit history** (P0) | Show live workflow stages; show tool calls, test output, diffs; log retries, errors, approvals, final outcome; let users inspect failed runs |
| F-07 | **Secure execution & limits** (P0) | Run repo code in isolated, ephemeral containers; restrict network, filesystem, CPU, memory, time; cap tool calls and repair attempts; keep secrets outside the agent's workspace; block writes to protected branches |

### 4.2 Future scope (V2+)

| Feature | Priority | Purpose |
|---------|----------|---------|
| Multi-language support (JS/TS first) | P1 | Expand beyond Python |
| Issue triage (label, prioritize) | P1 | Handle incoming issues automatically |
| Repository knowledge index (RAG) | P1 | Better retrieval for large codebases |
| Multiple model providers | P1 | Compare and route between LLMs |
| Benchmark & cost dashboard | P1 | Track resolve rate, latency, token cost |
| Scheduled / automatic issue processing | P1 | Process eligible issues under policy |
| Team workspaces & role-based access | P1 | Collaboration and permissions |
| Parallel agents | P2 | Try independent fix hypotheses |
| Cross-repo dependency analysis | P2 | Bugs spanning multiple packages |
| Automated security remediation | P2 | Carefully scoped vulnerability fixes |

V2 items are prioritized by evidence from MVP runs, not added to inflate the feature list.

---

## 5. User Stories & Acceptance Criteria

| ID | User story | Acceptance criteria |
|----|-----------|---------------------|
| US-01 | As a user, I want to connect my GitHub account so GitResolve can access my authorized repositories. | Sign-in works; unauthorized repos cannot be selected; access can be revoked. |
| US-02 | As a developer, I want to select an issue so the agent investigates a specific bug. | Issue is fetched and normalized; repo and issue IDs are stored. |
| US-03 | As a developer, I want to see the agent's plan before it runs so I understand the intended work. | Plan lists tasks, expected evidence, and stopping conditions. |
| US-04 | As a developer, I want the agent to reproduce the bug so the fix targets a real failure. | Reproduction command, outcome, and output are recorded. |
| US-05 | As a developer, I want the agent to patch and rerun tests so failures guide later attempts. | Failures are captured; retries are bounded; each attempt is traceable. |
| US-06 | As a developer, I want to review the diff and test results before a PR is created. | User can inspect changes and validation status, then approve or reject. |
| US-07 | As a reviewer, I want the PR to include test evidence so I can assess the fix quickly. | PR links the issue, describes changes, and reports real test results. |
| US-08 | As a developer, I want failed runs to explain why they stopped so I can recover. | Every failure has an explicit status and actionable explanation. |
| US-09 | As a repo owner, I want enforced execution limits and permissions so the agent cannot exceed its authorization. | Budgets and tool permissions are enforced server-side, independent of model instructions. |
| US-10 | As a maintainer, I want benchmark runs recorded so I can tell whether new versions improve. | Each run stores version, scenario, outcome, and metrics. |

### Core user journey

1. Sign in with GitHub and authorize repositories
2. Select a repository and an issue
3. Review the agent's plan and limits, then start the run
4. Watch live progress: reproduction, patch, tests
5. Review the diff and validation report
6. Approve or reject PR creation
7. GitResolve opens the PR (only if all gates pass) and shows the link and final outcome

If the bug can't be reproduced, tests fail, the patch exceeds its scope, or a security restriction triggers, the run must **pause or stop with a clear status**. A PR is never reported as created unless GitHub confirms it.

---

## 6. Key Success Metrics

Targets are provisional and should be validated against compute budget and benchmark difficulty. Evaluate on a **fixed benchmark** (for example a subset of SWE-bench Lite plus a seeded demo repo), not hand-picked examples.

| ID | Metric | Definition | Initial target |
|----|--------|-----------|----------------|
| M-01 | Issue resolution rate | % of eligible benchmark issues with a correct patch passing acceptance tests | ≥ 30% |
| M-02 | Regression test success | % of accepted patches passing relevant and existing required tests | ≥ 90% |
| M-03 | Workflow completion | % of eligible runs reaching a valid end state without an unhandled system error | ≥ 95% |
| M-04 | Unapproved protected actions | Unauthorized PRs, protected-branch writes, or other prohibited actions | **0** (hard requirement) |

**Supporting metrics:** reproduction success rate, median and p90 time per issue, average cost (tokens + compute) per issue, repair attempts per successful task, % of patches rejected at verification, % of PRs accepted without major rework, security-policy violations caught in testing.

**Reporting rule:** Always publish benchmark size, issue-selection criteria, baseline, and failure cases alongside any score. A small or cherry-picked set cannot support broad claims.

---

## 7. Safety & Trust Requirements (non-negotiable)

- **Repository security:** least-privilege GitHub permissions; never expose tokens or private code; treat issue text, repo files, dependencies, and command output as **untrusted input**.
- **Execution isolation:** disposable sandboxes with CPU, memory, disk, process, and time limits; network denied by default.
- **Change safety:** no auto-merge or deploy; protected branches enforced **outside** the LLM; human approval before PR creation; stop if verification can't be completed.
- **Transparency:** keep a trace of every tool call and real test result; distinguish verified facts from model hypotheses; label each run as succeeded, partially succeeded, or failed.

---

## 8. Open Items

1. **Timeline:** 2-3 weeks
2. **Autonomy level:** Human approves before PR creation
3. **Benchmark choice:** SWE-bench Lite subset

