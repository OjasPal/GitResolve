import type { Metadata } from "next";
import Link from "next/link";
import { LegalPage } from "@/components/LegalPage";

export const metadata:Metadata={
  title:"Help",
  description:"Connect GitHub, start a repair and understand each review, test and approval in GitResolve.",
};

export default function HelpPage(){
  return <LegalPage guide title="A clear path to your next fix."
    intro="From connecting your first repository to reviewing a tested patch. Find your place in the workflow and know what to do next."
    sections={[
    {title:"Getting started",content:<><p>GitResolve investigates a Python repository bug, prepares a repair and records evidence for your review. Start with <Link href="/login">Sign in</Link> or <Link href="/register">Get started</Link>. Returning users can open Workspace.</p>
<p>Choose Continue with GitHub or Google, or use email and password. New email users can register and returning email users can <Link href="/reset-password">reset a forgotten password</Link>. Use your original sign-in method if an account already exists with that email. Allow the sign-in popup and complete the provider&#39;s prompts.</p>
<p>Signing into GitResolve establishes your app account. Repository access also requires GitHub authorization and a GitResolve Agent installation.</p></>},
    {title:"Connect GitHub",content:<><ol><li>Open the workspace and follow the GitHub connection screen.</li>
<li>Choose Install GitHub App and install GitResolve Agent on the account or organization containing your repository.</li>
<li>Select the repositories you want to make available. Organization installations may require an administrator&#39;s approval.</li>
<li>Return to GitResolve and choose Authorize GitHub. Confirm the account you intend to use.</li>
<li>Open <Link href="/repositories">Repositories</Link>. If access has changed, refresh access or authorize again from Settings.</li></ol>
<p>Repository visibility follows both the installation and your GitHub permissions. A repository must be included in the installation and your signed-in GitHub account must have the required write, maintain or admin permission. The app cannot grant access you lack on GitHub.</p>
<p>If a repository is missing, check the selected GitHub account, installation owner, selected repositories and your permissions. Refresh after changing them. Ask an organization administrator about pending installation approval. If installation is unavailable in the UI, contact the app operator.</p></>},
    {title:"Find your way around",content:<><ul><li><Link href="/dashboard">Dashboard</Link> is your home base for repairs needing attention, active runs and recent tasks. Open a task to return to its evidence and current decision.</li>
<li><Link href="/tasks">Repair Tasks</Link> lists your runs. Use the available search and filters to find a repository, issue or status.</li>
<li><Link href="/repositories">Repositories</Link> lists the projects your account can access. Choose Fix an issue to open New Repair with that repository selected. Manage the installation on GitHub to change repository selection.</li>
<li><Link href="/tasks/new">New Repair</Link> starts an investigation.</li>
<li><Link href="/settings">Settings</Link> manages your display name, GitHub access, appearance and session.</li>
<li>The breadcrumb identifies your current workspace. Jump to… or Ctrl+K (⌘K on Mac) opens search for pages, repositories and recent repairs. On mobile, open the navigation drawer.</li></ul></>},
    {title:"Start a repair from an issue",content:<><ol><li>Open <Link href="/tasks/new">New Repair</Link> and choose the repository.</li>
<li>Switch to the GitHub issue option.</li>
<li>Select an existing open issue and inspect its preview. Use the available paging controls if needed.</li>
<li>Start analysis and wait for the task&#39;s plan.</li></ol>
<p>Choose the issue that describes the actual failure. If it is missing, check the selected repository and refresh its issues. You can create an issue on GitHub or use the direct description option.</p></>},
    {title:"Describe a bug",content:<><p>Choose the description option and write the expected behavior, actual behavior and steps to reproduce. Include the relevant error text, affected files or test command when known.</p>
<p>You can attach supporting screenshots or text files. The composer accepts PNG, JPG, JPEG, WebP, TXT, LOG, MD, JSON and YAML files. It allows up to four files, images up to 5 MB each, text files up to 128 KB each and 10 MB total. Wait for each attachment to become Ready. Remove and retry failed uploads. Exclude passwords, access tokens and private credentials.</p>
<p>Select the repository before starting. A description creates its own repair task without requiring a published GitHub issue.</p></>},
    {title:"Review the plan",content:<><p>The task moves through analysis, plan review, sandbox setup, investigation, reproduction, repair, testing and safety evaluation. Some paths can stop earlier. The stage rail shows where the task is, while the header shows its status and budget counters. Limits shown on your task apply to that run.</p>
<p>At Plan ready, read the Plan and use the Ask a question composer to understand the approach. It is one text area with a Send question action. Replies use this run&#39;s evidence. Asking a question keeps the task at the review gate.</p>
<p>Choose Start repair when satisfied, or Cancel when available. At the first gate, Edit original request lets you revise the starting description and request a fresh investigation. Review the resulting plan again before starting.</p></>},
    {title:"Follow the evidence",content:<><ul><li>Plan describes the investigation and proposed approach.</li>
<li>Evidence contains the recorded observations and reproduction information available for the run.</li>
<li>Diff shows the proposed file edits. Inspect the changed lines and their context.</li>
<li>Tests shows available commands, results and before/after evidence. A missing dependency or infrastructure failure can prevent a meaningful result. Read the recorded explanation and any skips.</li>
<li>Events records progress, tool activity and state changes in order. Use it to understand what the agent attempted and where progress stopped. </li>
<li>The sandbox is the isolated environment used to execute repository checks. Test execution has networking disabled. Sandbox time measures execution work and is separate from the model-call and token counters.</li></ul>
<p>An empty tab can mean the relevant stage has yet to produce an artifact. Use the status and Events together. Leaving the page or signing out allows a worker task to continue. Return through Dashboard or Repair Tasks.</p></>},
    {title:"Review the fix",content:<><p>At Fix ready, inspect the final Diff, Tests, Evidence and any limitations. Use the single Ask a question composer to ask about the patch or results. A reply keeps publication awaiting your decision.</p>
<p>Open the diff before approving when prompted. Choose Approve &amp; create draft PR, inspect the confirmation details and approve the exact patch shown. If the patch changed, reload and review its latest version. While a chat request is pending, wait for it to finish before using approval controls.</p>
<p>At this gate, changing the original request requires starting a new repair. The existing request editor explains that transition. Reject ends the proposed repair and Run again, where available, creates another task.</p></>},
    {title:"Open and review the pull request",content:<><p>Creating pull request means publication is in progress. Wait for the confirmed PR result. The Pull request tab and task action then open it on GitHub. Review the branch, changed files and recorded evidence there. Repository maintainers control merge decisions on GitHub. GitResolve requires your exact-diff approval before publication and leaves merging to maintainers.</p></>},
    {title:"Personalize Settings",content:<><p>Under Account, edit Display name and choose Save name. It accepts a nonempty name up to 80 characters and updates the app&#39;s account profile. Your provider&#39;s name is the initial default. The saved name is used by GitResolve&#39;s greeting and account menu.</p>
<p>Under GitHub access, manage the installation or refresh authorization. Under Appearance, choose Obsidian, Porcelain or System. Connection shows backend availability and includes a refresh action. Under Your session, sign out and confirm. Sign in again to return to your tasks.</p></>},
    {title:"When you get stuck",content:<><dl><dt>Sign-in popup closes or is blocked</dt><dd>Allow popups, retry and complete the provider prompts</dd>
<dt>Email already belongs to another sign-in method</dt><dd>Use the method originally used for that account</dd>
<dt>GitHub session expired</dt><dd>Authorize GitHub again</dd>
<dt>Repository or task is unavailable</dt><dd>Confirm your account, installation selection and repository permissions</dd>
<dt>No open issues</dt><dd>Use a direct description or create an issue on GitHub</dd>
<dt>A task is already active</dt><dd>Open the existing task offered by the app</dd>
<dt>Task stays queued</dt><dd>Check backend availability and return to its Events for an update</dd>
<dt>Live updates or requests fail</dt><dd>Use the displayed retry or refresh action and check the latest task status before resubmitting</dd>
<dt>Cannot reproduce</dt><dd>Inspect the baseline evidence and improve reproduction steps before starting another run</dd>
<dt>No fix found or budget reached</dt><dd>Read the recorded outcome and provide a narrower, reproducible issue</dd>
<dt>Infrastructure or sandbox failure</dt><dd>Read the recovery message and retry when the service is available</dd>
<dt>Safety check stopped the task</dt><dd>Inspect the explanation and proposed diff before deciding on a new run</dd>
<dt>Approval is unavailable or patch changed</dt><dd>Open the latest diff, wait for pending actions and review the current evidence</dd>
<dt>Publication fails</dt><dd>Inspect the task&#39;s current state and use its offered recovery action</dd>
<dt>Name fails to save</dt><dd>Check the validation message, connection and sign-in, then retry</dd></dl>
<p>Task status, evidence and displayed recovery actions should guide retries. For operator assistance, provide the task identifier, visible status, safe error message and reproduction steps. Remove credentials and sensitive repository contents from anything you share.</p></>}
    ]}/>
}
