# GitResolve Help content draft

Content approved on 2026-11-07. Implemented at /help using the shared policy reading layout. The public footer begins with Help, followed by Privacy, Terms and Your data. GitHub appears after FAQ in the public header and mobile navigation.

## Getting started

GitResolve investigates a Python repository bug, prepares a repair and records evidence for your review. Start from the public site's Sign in or Get started action. Returning users can open Workspace.

Choose Continue with GitHub or Google, or use email and password. New email users can register and returning email users can reset a forgotten password. Use your original sign-in method if an account already exists with that email. Allow the sign-in popup and complete the provider's prompts.

Signing into GitResolve establishes your app account. Repository access also requires GitHub authorization and a GitResolve Agent installation.

## Connect GitHub and install GitResolve Agent

1. Open the workspace and follow the GitHub connection screen.
2. Choose Install GitHub App and install GitResolve Agent on the account or organization containing your repository.
3. Select the repositories you want to make available. Organization installations may require an administrator's approval.
4. Return to GitResolve and choose Authorize GitHub. Confirm the account you intend to use.
5. Open Repositories. If access has changed, refresh access or authorize again from Settings.

Repository visibility follows both the installation and your GitHub permissions. A repository must be included in the installation and your signed-in GitHub account must have the required write, maintain or admin permission. The app cannot grant access you lack on GitHub.

If a repository is missing, check the selected GitHub account, installation owner, selected repositories and your permissions. Refresh after changing them. Ask an organization administrator about pending installation approval. If installation is unavailable in the UI, contact the app operator.

## Find your way around

- Dashboard is your home base for repairs needing attention, active runs and recent tasks. Open a task to return to its evidence and current decision.
- Repair Tasks lists your runs. Use the available search and filters to find a repository, issue or status.
- Repositories lists the projects your account can access. Choose Fix an issue to open New Repair with that repository selected. Manage the installation on GitHub to change repository selection.
- New Repair starts an investigation.
- Settings manages your display name, GitHub access, appearance and session.
- The breadcrumb identifies your current workspace. Jump to or Ctrl+K opens search for pages, repositories and recent repairs. On mobile, open the navigation drawer.

## Start a repair from an issue

1. Open New Repair and choose the repository.
2. Switch to the GitHub issue option.
3. Select an existing open issue and inspect its preview. Use the available paging controls if needed.
4. Start analysis and wait for the task's plan.

Choose the issue that describes the actual failure. If it is missing, check the selected repository and refresh its issues. You can create an issue on GitHub or use the direct description option.

## Start a repair with your own description

Choose the description option and write the expected behavior, actual behavior and steps to reproduce. Include the relevant error text, affected files or test command when known.

You can attach supporting screenshots or text files. The composer accepts PNG, JPG, JPEG, WebP, TXT, LOG, MD, JSON and YAML files. It allows up to four files, images up to 5 MB each, text files up to 128 KB each and 10 MB total. Wait for each attachment to become Ready. Remove and retry failed uploads. Exclude passwords, access tokens and private credentials.

Select the repository before starting. A description creates its own repair task without requiring a published GitHub issue.

## Understand the workflow and first approval gate

The task moves through analysis, plan review, sandbox setup, investigation, reproduction, repair, testing and safety evaluation. Some paths can stop earlier. The stage rail shows where the task is, while the header shows its status and budget counters. Limits shown on your task apply to that run.

At Plan ready, read the Plan and use the Ask a question composer to understand the approach. It is one text area with a Send question action. Replies use this run's evidence. Asking a question keeps the task at the review gate.

Choose Start repair when satisfied, or Cancel when available. At the first gate, Edit original request lets you revise the starting description and request a fresh investigation. Review the resulting plan again before starting.

## Read evidence while the repair runs

- Plan describes the investigation and proposed approach.
- Evidence contains the recorded observations and reproduction information available for the run.
- Diff shows the proposed file edits. Inspect the changed lines and their context.
- Tests shows available commands, results and before/after evidence. A missing dependency or infrastructure failure can prevent a meaningful result. Read the recorded explanation and any skips.
- Events records progress, tool activity and state changes in order. Use it to understand what the agent attempted and where progress stopped. It contains activity records rather than a private reasoning transcript.
- The sandbox is the isolated environment used to execute repository checks. Test execution has networking disabled. Sandbox time measures execution work and is separate from the model-call and token counters.

An empty tab can mean the relevant stage has yet to produce an artifact. Use the status and Events together. Leaving the page or signing out allows a worker task to continue. Return through Dashboard or Repair Tasks.

## Review the fix at the second approval gate

At Fix ready, inspect the final Diff, Tests, Evidence and any limitations. Use the single Ask a question composer to ask about the patch or results. A reply keeps publication awaiting your decision.

Open the diff before approving when prompted. Choose Approve & create draft PR, inspect the confirmation details and approve the exact patch shown. If the patch changed, reload and review its latest version. While a chat request is pending, wait for it to finish before using approval controls.

At this gate, changing the original request requires starting a new repair. The existing request editor explains that transition. Reject ends the proposed repair and Run again, where available, creates another task.

## Open and review the pull request

Creating pull request means publication is in progress. Wait for the confirmed PR result. The Pull request tab and task action then open it on GitHub. Review the branch, changed files and recorded evidence there. Repository maintainers control merge decisions on GitHub. GitResolve requires your exact-diff approval before publication and leaves merging to maintainers.

## Personalize Settings

Under Account, edit Display name and choose Save name. It accepts a nonempty name up to 80 characters and updates the app's account profile. Your provider's name is the initial default. The saved name is used by GitResolve's greeting and account menu.

Under GitHub access, manage the installation or refresh authorization. Under Appearance, choose Obsidian, Porcelain or System. Connection shows backend availability and includes a refresh action. Under Your session, sign out and confirm. Sign in again to return to your tasks.

## When you get stuck

| Symptom | Next step |
| --- | --- |
| Sign-in popup closes or is blocked | Allow popups, retry and complete the provider prompts |
| Email already belongs to another sign-in method | Use the method originally used for that account |
| GitHub session expired | Authorize GitHub again |
| Repository or task is unavailable | Confirm your account, installation selection and repository permissions |
| No open issues | Use a direct description or create an issue on GitHub |
| A task is already active | Open the existing task offered by the app |
| Task stays queued | Check backend availability and return to its Events for an update |
| Live updates or requests fail | Use the displayed retry or refresh action and check the latest task status before resubmitting |
| Cannot reproduce | Inspect the baseline evidence and improve reproduction steps before starting another run |
| No fix found or budget reached | Read the recorded outcome and provide a narrower, reproducible issue |
| Infrastructure or sandbox failure | Read the recovery message and retry when the service is available |
| Safety check stopped the task | Inspect the explanation and proposed diff before deciding on a new run |
| Approval is unavailable or patch changed | Open the latest diff, wait for pending actions and review the current evidence |
| Publication fails | Inspect the task's current state and use its offered recovery action |
| Name fails to save | Check the validation message, connection and sign-in, then retry |

Task status, evidence and displayed recovery actions should guide retries. For operator assistance, provide the task identifier, visible status, safe error message and reproduction steps. A support contact still needs to be chosen before the Help page is published.

## Editorial and implementation notes

This draft used GitResolve_AFD.md for journey structure and current frontend source for implemented behavior. The AFD's existing-issues-only scope, three-attempt limit and authentication details are older proposals. Current UI includes direct descriptions, attachments, Firebase sign-in and two approval conversations. Help should describe the shipped controls and task-specific limits.

Before publication, visually verify labels against the running app, confirm display-name persistence through provider reauthentication, and decide on the support contact. Proposed Help sections can become an anchored table of contents within the existing LegalPage-style public layout. Footer placement was approved and implemented.
