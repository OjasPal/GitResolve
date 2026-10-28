# GitResolve — App Flow Document (AFD)

**Version:** 1.1 · **Status:** Draft for review  
**Purpose:** Define the screens users navigate, access rules, key actions, and the main repair journey.  
**Sources:** `docs/PRD.md` and `docs/TRD.md`. The PRD/TRD remain the source of truth for product scope and technical behavior.

## 1. MVP Scope and UX Principles

- **Dashboard is home base.** Each repair has a persistent detail page; users can leave and return while the worker continues.
- **Two explicit human gates:** review the plan and click **Start repair**; later review the exact diff and evidence before **Approve & create PR**.
- GitResolve never merges a pull request.
- MVP supports existing GitHub issues in Python repositories. Free-text bug descriptions, team/org views, notifications, task deletion/archive, and in-app account deletion are deferred.
- A task that needs more information is terminal in the MVP. The user updates the GitHub issue and starts a new run.

## 2. Page Hierarchy and Routes

| Route | Access | Purpose |
|---|---|---|
| `/` | Public | Product overview and sign-in CTA |
| `/login` | Public | Start GitHub authorization |
| `/auth/callback` | Auth flow | Store session token, handle errors, route user |
| `/onboarding` | Protected | Install GitHub App on selected repositories |
| `/dashboard` | Protected | Needs attention, active/recent tasks, New Repair |
| `/repositories` | Protected | View accessible repositories; manage access on GitHub |
| `/tasks` | Protected | Task history, filters, and outcomes |
| `/tasks/new` | Protected | Select repository and existing GitHub issue |
| `/tasks/[taskId]` | Protected + task owner | Monitor, review, approve/reject, view PR result |
| `/settings` | Protected | Account details, manage GitHub access, sign out |
| Not Found / Error | As appropriate | Friendly error and recovery action |

### Signed-in navigation

- Sidebar: **New Repair** (primary action), Dashboard, Repositories, Repair Tasks, Settings.
- On narrow screens, collapse the sidebar into a top bar/drawer.
- Task detail pages show a breadcrumb: `Repair Tasks / owner/repo #issue`.
- Show an attention indicator when the loaded task list includes `awaiting_start` or `awaiting_approval`.
- Public pages use a minimal header with Sign in.

## 3. Authentication and Access Rules

- Sign-in and repository access are separate: users sign in with GitHub, then install the GitHub App on repositories they choose.
- Protected pages use a client-side AuthGate for navigation UX only. **The backend enforces authentication, repository access, and task ownership on every protected API call.**
- API calls send `Authorization: Bearer <JWT>`. The MVP JWT lasts 60 minutes; no silent refresh. On `401`, clear the token and redirect to `/login?next=...`.
- Treat `403` and `404` for tasks/repositories as the same message: “This item doesn't exist or you don't have access.”
- The browser must never receive GitHub App credentials, GitHub access tokens, or LLM API keys. The session JWT is stored in `sessionStorage` as specified in the TRD.
- OAuth callback errors should redirect to the frontend with a readable error. **Confirm this callback behavior in the TRD before implementation.**
- After GitHub App installation or access changes, re-authenticate/refresh access to sync repository access; confirm the exact flow in the TRD.

## 4. Screen Essentials

| Screen | Main content and actions |
|---|---|
| Landing | Value proposition, how it works, safety principles, Sign in with GitHub |
| Login | Continue with GitHub; explain that users choose accessible repositories and approve each proposed diff |
| Onboarding | Explain App installation and permissions; choose repositories on GitHub |
| Dashboard | New Repair; tasks needing attention; active tasks; latest 10 tasks; repository summary |
| Repositories | Accessible repositories, visibility/default branch, last task status, refresh access, Fix an issue |
| Tasks | Repository, issue, status, attempts, created time/duration; status/repository filters; Load more |
| New Repair | Choose repository, select/search open issue, preview issue, Analyze issue |
| Task details | Status, stage tracker, plan, timeline, reproduction results, attempts, diff, test evidence, PR result |
| Settings | GitHub account, manage repository access on GitHub, sign out, retention notice |
| Error pages | Friendly explanation, Retry or return to Dashboard/Home; never show stack traces or secrets |

## 5. Main User Journeys

### First visit
Landing → Sign in → GitHub authorization → callback → onboarding if no accessible repositories → install GitHub App → refresh/re-authenticate → Dashboard.

### Start a repair
Dashboard or Repositories → New Repair → choose repository → choose existing open issue → **Analyze issue** → task detail page. The agent creates a plan but does not execute the repair until the user clicks **Start repair**.

### Monitor and review
Task page shows progress and evidence. When the plan is ready, user reviews it and starts the repair. When a validated fix is ready, user inspects the diff, before/after test results, flags, and limitations.

### Approve or reject
**Approve & create PR** opens a confirmation showing target branch, files/lines changed, test summary, and diff hash. On confirmation, GitResolve creates the branch and pull request. The PR link appears only after GitHub confirms creation. **No automatic merge.** Reject ends the task; the user may run again.

### Return later
Dashboard shows tasks needing attention. Each task has a stable detail URL. Leaving the browser does not stop a worker task. “Run again” always creates a new task for the same repository and issue.

### Manage repository access
Settings or Repositories → manage installation access on GitHub → return to GitResolve → refresh access.

## 6. Repair Task States and Actions

| State | User-facing meaning | Available action |
|---|---|---|
| `queued` | Waiting for worker | Cancel |
| `planning` | Analyzing issue and preparing plan | Cancel |
| `awaiting_start` | Plan ready; user decision required | Start repair or Cancel |
| `running` | Agent is investigating, patching, or testing | Cancel |
| `awaiting_approval` | Fix ready for human review | Approve & create PR or Reject |
| `creating_pr` | Creating pull request | No action; wait |
| `completed` | PR created successfully | Open on GitHub |
| `rejected` | User rejected proposed fix | Run again |
| `needs_input` | Issue lacks necessary detail | Update issue on GitHub, then Run again |
| Failure / `cancelled` | Repair stopped without successful PR | Review evidence; use case-specific recovery or Run again |

Typical workflow: `queued → planning → awaiting_start → running → awaiting_approval → creating_pr → completed`. Failures, rejection, and cancellation can end the flow earlier. The agent has a maximum of **3 repair attempts** and a **20-minute task limit**, as specified in the source document.

## 7. Important Task-Page Behavior

- Show repository/issue, status, current stage, attempts used/max, elapsed time, and model-call/time budget.
- Show plan, timeline, reproduction outcome, each attempt's diff/test summary, final diff, before/after test evidence, known limitations, and PR link when available.
- Poll task events every **3 seconds** while active; every **15 seconds** when the browser tab is hidden. Stop polling at `awaiting_start`, `awaiting_approval`, or terminal states; resume after a user action.
- Disable action buttons while a request is processing to prevent duplicate submissions.
- Require the user to open the diff section before enabling approval.
- Approval must verify the reviewed diff hash. If it changed, ask the user to reload and review again.
- “Run again” creates a new task; never reopen or overwrite the old task.

## 8. Failure, Loading, and Empty States

- **Empty lists:** explain what is missing and provide the next action (for example, New Repair or Choose repositories).
- **Loading:** skeletons for lists and task sections; buttons show a spinner and are disabled.
- **Request error:** inline message with Retry; preserve the rest of the page and last loaded data.
- **Session expired (`401`):** explain that sign-in is needed and return the user to their intended page after login.
- **No access (`403`/`404`):** use the same generic access/not-found message.
- **No open issues:** ask the user to create an issue on GitHub.
- **Active task already exists (`409`):** link to the existing task.
- **Daily limit (`429`):** show the limit and when to retry; do not retry indefinitely.
- **GitHub unavailable (`502`):** show a retry option.
- **Worker queued for over 60 seconds:** show a hint that the worker may be unavailable.
- **Events fail repeatedly:** show “Live updates paused” and a Retry action; keep the last known state visible.
- Never show raw stack traces, credentials, tokens, or secrets.

## 9. Route Transitions

| From | Action | Destination |
|---|---|---|
| Landing | Sign in | `/login` → GitHub → `/auth/callback` |
| Callback | Successful login | `/onboarding`, saved `next` path, or `/dashboard` |
| Dashboard | New Repair | `/tasks/new` |
| Dashboard / Tasks | Open task | `/tasks/[taskId]` |
| Repositories | Fix an issue | `/tasks/new?repo=<repoId>` |
| New Repair | Analyze issue | `/tasks/[taskId]` |
| Task details | Run again | `/tasks/new?repo=<repoId>&issue=<number>` |
| Task details | Open issue/PR | GitHub in a new tab |
| Any page | Sign out | `/` |
| Any protected API call | `401` | `/login?next=<path>` |

## 10. Assumptions and Open Decisions

**MVP assumptions:** GitHub App authentication; JWT in `sessionStorage`; Python repositories and existing GitHub issues only; one active task per user; maximum 10 tasks per day; 3-second active-task polling.

**Confirm against the TRD before implementation:**
1. OAuth callback errors redirect to the frontend with an error code.
2. Repository access is refreshed after GitHub App installation or permission changes.
3. PR creation failures after approval have a supported retry path.
4. The exact retention periods and task-state names match the implemented backend.

**Out of scope for MVP:** free-text bug descriptions, team/org dashboards, push/email notifications, task deletion/archive, account deletion in-app, and automatic PR merging.
