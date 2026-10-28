# GitResolve: Implementation Plan

**Version:** 1.0 | **Status:** Draft for team execution | **Doc 6 of 6**

## 1. Purpose and Approach

This document defines the implementation sequence, team ownership, and completion criteria for the GitResolve MVP.

Follow the PRD, TRD, AFD, and Backend Schema as the project’s source of truth. Build a functional end-to-end demo before investing in detailed UI/UX design.

**Core stack**
- Frontend: Next.js, TypeScript, React, Tailwind CSS — Vercel
- Backend: FastAPI — Google Cloud Run
- Database: Google Cloud Firestore
- Agent: LangGraph with Gemini as the primary model
- Execution: Separate, isolated Docker worker
- Source control: GitHub App and GitHub API

**Core safety rule:** GitResolve must receive explicit user approval of the exact diff before creating a branch or pull request. It must never auto-merge.

## 2. Team Responsibilities

### Member 1 — AI Agent & LangGraph
- Design LangGraph workflow and shared agent state.
- Implement issue analysis, repository investigation, and bug localization.
- Build patch generation and the bounded repair loop.
- Integrate Gemini/Groq and evaluate agent performance.
- Debug failed runs and improve agent behavior.

### Member 2 — Backend & Database
- Implement FastAPI endpoints, validation, and authorization.
- Build Firestore collections and the centralized store layer.
- Manage task lifecycle, events, artifacts, and worker dispatch.
- Implement authentication and deploy the API to Cloud Run.
- Manage production configuration and secrets.

### Member 3 — Execution Sandbox & GitHub
- Build isolated Docker-based code execution.
- Enforce execution limits and sandbox security policies.
- Implement GitHub App integration and repository operations.
- Handle safe branch and commit creation.
- Create pull requests only after valid user approval.

### Member 4 — Frontend & Product Integration
- Build the Next.js frontend and application routes.
- Implement login, onboarding, repositories, and task pages.
- Build progress, plan review, logs, diff review, and approval UI.
- Integrate frontend with the backend and deploy to Vercel.
- Lead UI/UX refinement after the functional demo.

### Member 5 — Testing, Evaluation & Integration
- Create seeded-bug repositories and reproducible test cases.
- Automate end-to-end tests for the repair workflow.
- Evaluate patch quality, regression tests, retry limits, and failures.
- Validate API contracts and cross-component integration.
- Test authorization, approval gates, sandbox isolation, and secret redaction.
- Maintain test results and track reproducible defects.

## 3. Implementation Phases

### Phase 1 — Contracts and Local Skeleton

**Goal:** Establish shared interfaces and a runnable development setup.

1. Review the PRD, TRD, AFD, and Backend Schema together.
2. Confirm API contracts, task states/stages, error formats, and unresolved decisions.
3. Create the agreed repository structure and local setup instructions.
4. Set up FastAPI, Next.js, the Firestore store interface, and the worker entry point.
5. Configure local environment variables and secret-handling rules.
6. Prepare shared mock tasks or fixtures for parallel development.

**Owners:** Member 2 leads contracts and API setup; Member 4 establishes the frontend shell. Members 1 and 3 define agent and worker interfaces; Member 5 prepares the initial test plan.

**Exit criteria**
- Components start locally.
- Shared schemas and task-state definitions are agreed.
- Health checks pass and no secrets are committed.

### Phase 2 — Agent Workflow and Safe Execution

**Goal:** Demonstrate a bounded repair workflow on a controlled repository.

1. Implement LangGraph state and workflow stages: intake, planning, setup, baseline, localization, reproduction, patching, testing, evaluation, and policy checks.
2. Implement the plan-review gate; execution must wait for the user's explicit start action.
3. Create seeded bugs with expected regression tests.
4. Execute repository commands only through the separate sandbox.
5. Enforce resource limits, bounded retries, diff limits, and test-result checks.
6. Persist stage transitions and structured attempt evidence.
7. Test success, failure, timeout, and inconclusive outcomes.

**Owners:** Member 1 leads the agent; Member 3 leads sandbox execution; Member 2 supports persistence and dispatch; Member 5 validates results.

**Exit criteria**
- At least one seeded bug is reproduced, patched, and tested.
- Failed runs stop safely and report a useful outcome.
- Untrusted code cannot access secrets or the host Docker socket.

### Phase 3 — Backend, Database, and GitHub Integration

**Goal:** Make repair tasks persistent, authenticated, and capable of creating approved PRs.

1. Implement GitHub sign-in and GitHub App repository-access checks.
2. Implement Firestore collections and centralized store functions.
3. Build task creation, idempotency, ownership checks, state transitions, events, and artifact retrieval.
4. Connect API task dispatch to the independent worker.
5. Implement repository and issue retrieval using authorized GitHub access.
6. Integrate safe branch, commit, and PR operations.
7. Enforce approval against the exact final diff hash before PR creation.
8. Add input validation, consistent errors, safe logging, and secret redaction.
9. Test persistence, duplicate requests, authorization failures, and recovery at supported stage boundaries.

**Owners:** Member 2 leads backend and Firestore; Member 3 leads GitHub operations; Member 1 integrates agent state; Member 5 validates contracts and failure paths.

**Exit criteria**
- Authenticated users can select an accessible repository and existing issue.
- Task state and evidence persist across page reloads.
- A PR cannot be created without explicit approval of the matching diff.

### Phase 4 — Functional Frontend and End-to-End Demo

**Goal:** Complete the primary user journey with real backend integration.

1. Implement the AFD routes: landing, login/callback, onboarding, dashboard, repositories, tasks, new repair, task details, settings, and error states.
2. Connect repository and issue selection to task creation.
3. Display the proposed plan and implement the explicit **Start repair** action.
4. Show task status, timeline, attempts, test evidence, and failure information.
5. Implement diff review with changed-file summaries and test results.
6. Add explicit approve/reject actions and display the PR link only after confirmed creation.
7. Implement loading, empty, error, and responsive states.
8. Run the full workflow against seeded-bug repositories and fix integration failures.

**Owners:** Member 4 leads frontend integration; Members 1–3 support their respective components; Member 5 leads end-to-end validation.

**Exit criteria**
- The demo works from sign-in through issue selection, plan review, repair, diff review, approval, and PR creation.
- Rejecting a fix does not create a PR.
- Task status remains understandable after refresh or reopening the task.

### Phase 5 — UI/UX Design, Hardening, and Release

**Goal:** Refine the working product, validate safety, and prepare the hosted demo.

**Why Document 4 was deferred:** The UI/UX Design Brief was intentionally postponed. Designing the full experience before the agent workflow, backend integration, and frontend behavior exist would mean designing around assumptions. Complete the functional demo in Phases 1–4 first, then create Document 4 based on the real workflow, states, evidence, errors, and approval journey.

1. Create Document 4 — UI/UX Design Brief — using the functional demo as the reference.
2. Refine information hierarchy, task progress, plan review, diff/test evidence, approval, and recovery states.
3. Run security checks for authorization, task transitions, approval gates, sandbox boundaries, and secret handling.
4. Run seeded-bug acceptance tests and record results.
5. Deploy FastAPI to Cloud Run and the frontend to Vercel.
6. Configure production secrets through Secret Manager.
7. Verify the hosted demo and document known limitations.
8. Stop unused paid resources and monitor cloud spending.

**Owners:** Member 4 leads UI/UX refinement; Member 5 leads validation; Members 1–3 harden their respective components. Member 2 coordinates backend deployment.

**Exit criteria**
- The hosted demo passes the critical workflow and security checks.
- UI/UX decisions reflect the implemented product.
- Known gaps and unverified behavior are documented honestly.

## 4. Integration Rules

- Agree on API schemas, enums, and task-state transitions before dependent components are implemented.
- Only the centralized store layer accesses Firestore.
- Backend authorization is mandatory; frontend route guards are not a security boundary.
- Never run untrusted repository code inside the FastAPI process or worker process.
- Keep vector search optional until benchmarks show it improves on lexical repository search.
- Mid-run checkpoint/resume, free-text bug reports, notifications, and auto-merge remain outside the MVP.
- Member 5 tests continuously throughout development, not only at the end.

## 5. Definition of Done

- [ ] Primary repair journey works from GitHub issue selection to approved PR.
- [ ] Plan and exact-diff approval gates are enforced server-side.
- [ ] Seeded regression tests verify repair behavior and failure handling.
- [ ] Authentication, authorization, sandbox, and secret-handling checks pass.
- [ ] Frontend, API, worker, and database follow agreed contracts.
- [ ] Deployment and demo instructions are documented.
- [ ] UI/UX Design Brief is completed after the functional demo and its refinements are verified.
