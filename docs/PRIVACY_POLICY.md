# Privacy Policy for GitResolve

> **LEGAL NOTICE AND DRAFT STATUS**  
> *This document is a working administrative and technical draft prepared for GitResolve and is subject to formal review and revision by qualified legal counsel. This document does not constitute formal legal certification, compliance validation, or binding legal advice. Applicable legal frameworks considered in the preparation of this draft include the Digital Personal Data Protection Act, 2023 (DPDPA 2023) of India, relevant provisions of the Information Technology Act, 2000, and standard international data privacy principles. Organizations deploying GitResolve must consult qualified legal counsel to adapt this document to their specific legal jurisdiction, corporate entity, and deployment configuration.*

---

## 1. Introduction and Scope

GitResolve ("we", "us", "our", or the "Platform") is an automated, AI-assisted software repair and issue resolution platform. The Platform interfaces with developer code repositories to analyze issue descriptions, formulate investigation plans, reproduce software defects in isolated sandbox environments, propose unified diff code patches, and publish draft pull requests upon human approval.

This Privacy Policy explains how GitResolve collects, processes, stores, protects, and discloses personal data and technical information when individuals ("users", "you", or "developers") interact with:
- The GitResolve web user interface (Next.js frontend);
- The GitResolve RESTful API (FastAPI backend);
- The GitResolve background task worker and execution pipeline;
- The GitResolve GitHub App integration; and
- Associated agentic workflows and execution sandboxes.

This Policy applies to data processed across both local development fixtures and cloud-hosted production deployments of GitResolve.

---

## 2. Operational Status and Architecture Overview

To provide full transparency regarding our technical architecture, GitResolve explicitly distinguishes between features that are currently implemented in the codebase, partially implemented, operating as local development fixtures, or planned for future releases:

| Feature / Subsystem | Current Architectural Status | Description |
| :--- | :--- | :--- |
| **GitHub App Authentication & Access** | **Implemented** | Authenticates via GitHub App OAuth flow; verifies user collaborator permissions on repositories; acquires repository snapshots via temporary authenticated HTTPS archives. |
| **Firebase User Authentication** | **Implemented (Client)** | Frontend Google Sign-In and Email/Password authentication via Google Firebase Auth SDK. |
| **Backend Session Management** | **Implemented** | Issues signed HS256 JWT session tokens (`jwt_ttl_minutes = 60`) with cryptographically validated claims. |
| **Firestore Native Storage** | **Implemented** | Server-side persistence of users, tasks, events, and artifacts using Google Cloud Firestore with client access strictly blocked. |
| **In-Memory / Local Storage** | **Implemented (Dev/Test)** | Single-process memory store for local development and testing (`STORE_BACKEND=memory`). |
| **AI Model Routing & Failover** | **Implemented** | Vertex AI routing using Gemini (Primary), Gemini 4 / 3.8 Flash (Technical Failover), and Gemini (Specialist Escalation). |
| **Docker Sandbox Execution** | **Implemented** | Bounded container execution with strict network isolation (`--network none`), read-only root filesystems, and dropped capabilities. |
| **Human Review Gates** | **Implemented** | Mandatory human approval required at Plan Review (Gate 1) and Cryptographic Diff Verification (Gate 2). |
| **Data Redaction Engine** | **Implemented** | Regex-based sanitization of API keys, access tokens, private keys, and passwords before logging or storing events and artifacts. |
| **Read-Time Retention Filtering** | **Implemented** | Query-level exclusion of tasks older than 90 days and subdocuments older than 30 days. |
| **Automated Physical TTL Purging** | **Partially Implemented** | Dependent on Google Cloud Firestore TTL infrastructure policies; no internal database deletion daemon is currently executed in-app. |
| **In-App Account Deletion** | **Planned (Not Implemented)** | No self-service button or API route currently exists to delete user profiles or task history. |
| **In-App GitHub Disconnection** | **Planned (Not Implemented)** | Disconnection must be executed directly by the user within their GitHub account settings. |

---

## 3. Data Collected and Purposes of Processing

We collect only the categories of data strictly necessary to provide, secure, and operate the GitResolve platform.

### 3.1. Account and Authentication Information
When you sign in to GitResolve, we collect:
- **GitHub Account Information**:
  - GitHub User ID (numerical identifier, stored as `githubUserId`);
  - GitHub Username / Handle (stored as `githubLogin`);
  - Profile Avatar URL (`avatarUrl`);
  - Authorized GitHub App Installation Identifiers (`installationIds`).
- **Firebase Authentication Profile (Frontend)**:
  - Firebase User Identifier (`uid`);
  - Email Address (where email authentication or Google sign-in is used);
  - Display Name;
  - Profile Photo URL;
  - Authentication Provider Identifier (`providerId`).
- **Session Tokens**:
  - Ephemeral JSON Web Tokens (JWT) minted by the backend to maintain authenticated API access across user sessions.

**Purpose**: To verify developer identity, maintain authorized sessions, validate repository collaborator permissions, prevent unauthorized access, and associate created repair tasks with their rightful owners.

### 3.2. Repository, Issue, and Source Code Data
To inspect, reproduce, and repair software defects, GitResolve processes:
- **Repository Metadata**: Repository names (`owner/repo`), repository ID, default branch name, visibility (public or private), and primary programming language.
- **Issue Content**: Issue numbers, issue titles, issue descriptions/bodies (captured up to a maximum limit of 20,000 characters; longer texts are truncated), issue labels, and issue comments (up to 10 comments, maximum 2,000 characters per comment).
- **Source Code Snapshots**: Upon task dispatch, GitResolve resolves the target default branch commit SHA (`baseSha`) and downloads a pinned source code archive (tarball) from `codeload.github.com`.
- **Target Source Files and Unified Diffs**: Patches and modified code snippets generated during repair attempts.

**Purpose**: To provide the necessary technical context for the AI model router to generate investigation plans, isolate defective code, execute test suites, and construct unified diff patches.

### 3.3. Task Execution, Artifacts, and Operational Events
During task lifecycle execution, the platform generates and stores:
- **Task Records**: Target repository ID, issue reference, task status, execution stage, timeout constraints, failure classifications, and approval records.
- **Investigation Plans**: Structured summaries of reported issues, identified files, and recommended inspection steps.
- **Execution Artifacts**: Generated reproduction test scripts (`repro_test`), iteration diffs (`attempt_*_diff`), test execution logs (`attempt_*_tests`), the final candidate patch (`final_diff`), final test outputs (`final_tests`), and published pull request references (`github_pr`).
- **Event Audit Logs**: Granular timeline events recording status changes, stage progressions, model invocations, tool executions, sandbox exit codes, policy checks, and human approvals.
- **Cryptographic Hashes**: SHA-256 hashes of generated patches (`finalDiffHash`) used to ensure that the exact code reviewed by the human developer is what gets submitted to GitHub.

**Purpose**: To provide real-time status reporting on the user dashboard, support developer review of proposed changes, ensure cryptographic integrity of deployed patches, and maintain system auditing records.

### 3.4. Technical and Network Telemetry
When communicating with the GitResolve API, our infrastructure records:
- **Internet Protocol (IP) Address**: Evaluated in-memory via sliding-window rate limiters to defend against denial-of-service and brute-force attacks (rate limit: 300 requests/minute per IP; 30 write operations/minute per user). Client IPs are extracted through reverse-proxy hop counts (`trusted_proxy_hops`) to prevent header spoofing.
- **HTTP Request Headers & Cookies**: Standard browser headers (User-Agent, Content-Type), idempotency keys, authorization headers, and the transient OAuth state cookie (`gitresolve_oauth_state`, HttpOnly, SameSite=Lax, Secure in production).
- **Application Server Logs**: Diagnostic logs detailing API routing, worker polling, lease acquisition, and exception traces.

---

## 4. Firebase Authentication and Firestore Data Handling

GitResolve employs Google Cloud services for user authentication and state storage:

### 4.1. Firebase Authentication
- The frontend client utilizes the Firebase JavaScript SDK for user authentication.
- When you authenticate via Google Sign-In or Email/Password, your authentication credentials are submitted directly to Google's Identity Platform.
- Firebase Auth persists session state locally in the user's browser local storage (`browserLocalPersistence`).

### 4.2. Cloud Firestore Storage and Security Boundary
- Backend persistent data is managed through Google Cloud Firestore in Native mode (`STORE_BACKEND=firestore`).
- **Direct Client Access is Denied**: As codified in `firestore.rules`, client-side read and write requests are categorically prohibited:
  ```javascript
  rules_version = '2';
  service cloud.firestore {
    match /databases/{database}/documents {
      match /{document=**} {
        allow read, write: if false;
      }
    }
  }
  ```
- All database interactions are mediated exclusively by the authenticated GitResolve backend server SDK using Google Cloud Identity and Access Management (IAM) service account credentials.
- **Collections Stored**:
  - `users/{userId}`: User identity and authorized installation mappings.
  - `tasks/{taskId}`: High-level repair task state, parameters, and status.
  - `tasks/{taskId}/events/{seq}`: Subcollection containing chronological execution events.
  - `tasks/{taskId}/artifacts/{artifactId}`: Subcollection containing investigation plans, diffs, test logs, and PR results.

---

## 5. GitHub Integration, Permissions, and Access Boundaries

GitResolve interacts with GitHub via a registered **GitHub App** rather than generic user personal access tokens.

### 5.1. Scope of Permissions Requested
The GitResolve GitHub App requests the following bounded repository permissions:
- **Metadata**: Read-only (required by GitHub for repository discovery).
- **Issues**: Read-only (required to fetch issue titles, descriptions, and discussion comments).
- **Repository Contents**: 
  - Read-only during repository inspection and archive checkout;
  - Read/Write strictly when publishing an approved patch to a dedicated feature branch.
- **Pull Requests**: Read/Write strictly to open a **draft pull request** containing the approved patch.
- **Explicitly Excluded Permissions**: GitResolve does not request, access, or require permissions for GitHub Actions, organization administration, repository deletion, repository settings, branch deletion, webhook administration, or automated pull request merging.

### 5.2. Collaborator Verification Boundary
Membership in a GitHub App installation does not automatically grant a user access to execute tasks on a repository. For every repository and issue query, the GitResolve backend queries GitHub's Collaborator API (`/repos/{owner}/{repo}/collaborators/{username}/permission`) using the authenticated user's immutable GitHub numerical ID. The user must personally possess at least `read`, `triage`, `write`, `maintain`, or `admin` permissions to initiate planning, and must possess `write`, `maintain`, or `admin` permissions before any pull request may be dispatched.

### 5.3. Credential-Free Execution Boundary
- **User OAuth Tokens**: The OAuth token obtained during user login is used solely to discover authorized installations and is immediately discarded. It is **never** written to the database or logged.
- **Installation Tokens**: Short-lived GitHub App installation tokens are minted on-demand using the App's private RSA key, scoped specifically to the single target repository, and held only in host memory.
- **Sandbox Isolation**: No GitHub App credentials, private keys, or installation tokens are ever mounted into the Docker sandbox or passed to LLM prompts. Code checkouts are downloaded as static tarballs directly on the host and unpacked into isolated temporary directories without `.git` credentials.

---

## 6. Third-Party AI Providers (Google Gemini & Google Cloud Gemini)

GitResolve integrates large language models (LLMs) to perform issue analysis, test generation, code synthesis, and pull request documentation.

### 6.1. AI Provider Infrastructure and Hosted Environment
All artificial intelligence model interactions in GitResolve are routed through **Google Cloud Vertex AI** enterprise infrastructure:
1. **Primary Model**: Google Gemini 3.8 Flash (`gemini-3.8-flash`), accessed through Google Cloud.
2. **Technical Fallback Model**: Google Gemini 3.6 Flash (`gemini-3.6-flash`), used after eligible technical failures of the primary model.
3. **Conditional Reasoning Specialist**: Google Gemini 4 Argon (`gemini-4-argon`), used for a bounded review of inadequate successful output or an inconclusive evaluation, subject to provider health checks.

Approval-gate conversations transmit the user's questions and bounded plan, patch and test evidence to these providers. Conversation messages are retained with the task record.

### 6.2. Data Transmitted to AI Providers
When a task is processed, the following data is transmitted via encrypted HTTPS to Google Cloud Vertex AI:
- Repository name and primary language;
- Issue number, title, and sanitized description;
- Issue comments relevant to the bug report;
- Execution error traces and pytest test outputs produced during reproduction;
- Unified diff code proposals generated during repair loops.

### 6.3. AI Provider Data Governance and Training Disclosures
- **Model Training Terms**: Subject to the enterprise agreement with Google Cloud and applicable Google Cloud Vertex AI terms (including the Google Cloud Data Processing Addendum and Google Cloud on Vertex AI partner terms), customer prompts and generated completions are treated as customer proprietary data and are not used by default to train or fine-tune public foundation models without customer instruction or consent. Deploying organizations must verify their specific Google Cloud organization settings, data governance policies, and contractual commitments.
- **Stateless Inference & Logging**: Model calls are processed via stateless HTTPS API transactions; providers do not maintain persistent memory of customer code between discrete requests, subject to standard cloud infrastructure operational, security, and abuse-monitoring logging policies maintained by Google Cloud.
- **Transmission Security**: All model requests and responses are encrypted in transit using Transport Layer Security (TLS 1.2 or TLS 1.3).

---

## 7. Sandbox Execution, Log Handling, and Sensitive Data Redaction

### 7.1. Docker Sandbox Execution Environment
Software reproduction and test execution occur exclusively within isolated Docker containers (`gitresolve-sandbox:poc` or `gitresolve-sandbox:streamlit`):
- **Network Isolation**: Containers are started with `--network none`. The sandbox has **zero network access**, preventing unauthorized data exfiltration or external network requests during code execution.
- **Filesystem Constraints**: The container root filesystem is strictly read-only (`--read-only`). Writable areas are limited to temporary in-memory tmpfs mounts (`/tmp` 256MB, `/workspace` 1GB).
- **Privilege Separation**: Containers run as an unprivileged non-root user (`10001:10001`) with all Linux capabilities dropped (`--cap-drop ALL`) and `no-new-privileges` enforced.
- **Resource and Process Limits**: Containers are restricted to 2 CPU cores, 2GB memory, 256 process IDs (PIDs), and bounded execution timeouts (default: 300 seconds).

### 7.2. Sensitive Data Redaction Engine
GitResolve enforces an automated, multi-pattern redaction engine (`app.core.redaction`) across all task outputs, container logs, and event messages before they are stored or streamed to the client interface:
- RSA and OpenSSH Private Keys (`-----BEGIN ... PRIVATE KEY-----`);
- Authorization header tokens (`Authorization: Bearer ...`);
- GitHub Personal Access Tokens and OAuth credentials (`ghp_*`, `github_pat_*`);
- Google Cloud API keys (`AIza*`);
- OpenAI and Google Cloud API keys (`sk-*`);
- JSON Web Tokens (`eyJ*`);
- Generic passwords, tokens, and secret assignments.

Redacted strings are replaced with `[REDACTED]` and log messages are capped to prevent memory buffer overflows.

---

## 8. Human Review and Approval Gates

To protect repository stability and prevent unauthorized or unintended modifications, GitResolve enforces two mandatory **Human-in-the-Loop (HITL)** approval gates:
1. **Plan Review Gate (Gate 1)**: Following automated issue analysis, the task transitions to `awaiting_start`. Execution completely pauses until an authorized human reviewer reviews the investigation plan and explicitly triggers repair execution via the dashboard.
2. **Cryptographic Diff Approval Gate (Gate 2)**: Following code synthesis and sandbox test verification, the task transitions to `awaiting_approval`. A SHA-256 cryptographic digest of the complete unified diff is computed (`finalDiffHash`). A human reviewer must inspect the exact line-by-line diff and submit an approval payload matching that specific hash. If the diff has been altered or tampered with, approval is rejected with a `409 Conflict`.
3. **No Autonomous Merging**: GitResolve publishes exclusively **draft pull requests** to a dedicated feature branch. The platform possesses no functionality to approve its own pull requests, merge code into protected branches, or delete existing branches.

---

## 9. User Rights and Data Deletion

### 9.1. Current Deletion Capabilities (Implementation Reality)
- **In-App Account Deletion**: GitResolve does **not** currently implement an automated, self-service account deletion API endpoint or user interface button.
- **In-App Task Deletion**: GitResolve does **not** currently implement an on-demand task deletion API.
- **Task Cancellation**: Users may cancel active tasks at any time (`POST /tasks/{id}/cancel`), which terminates worker execution and sandbox containers, but retains the historical audit record until expiry.

### 9.2. Exercising Rights under the Digital Personal Data Protection Act, 2023 (DPDPA)
If you are accessing GitResolve within the territory of India or are protected by the provisions of the DPDPA 2023, you possess specific statutory rights regarding your digital personal data:
- **Right to Access Information**: You have the right to request a summary of personal data processed by GitResolve and the identities of data fiduciaries/processors with whom it has been shared.
- **Right to Correction and Erasure**: You have the right to request the correction of inaccurate personal data and the erasure of your personal data that is no longer necessary for the purpose for which it was collected.
- **Right of Grievance Redressal**: You have the right to register a grievance with our designated Grievance Officer regarding the processing of your personal data.
- **Right to Nominate**: You have the right to nominate another individual to exercise your rights in the event of death or incapacity.

### 9.3. Manual Data Deletion Procedure
Until automated self-service account deletion is implemented in a future release, users wishing to request the manual erasure of their account data, stored task records, and associated event logs may submit a written request to:
- **Designated Contact**: `[privacy@gitresolve.example.com]` *(Placeholder - replace prior to deployment)*
- **Subject Line**: "DPDPA Data Erasure Request - [GitHub Username]"

Upon verification of your GitHub identity, our administrators will manually delete your user profile document from `users/{userId}` and purge your associated task documents and subcollections from Google Cloud Firestore.

---

## 10. GitHub Disconnection and Revocation of Access

You may revoke GitResolve's access to your GitHub account and repositories at any time directly through GitHub:
1. Log in to your GitHub account and navigate to **Settings > Applications > Installed GitHub Apps**.
2. Locate **GitResolve** and click **Configure**.
3. Under "Repository access", you may remove specific repositories, or scroll to the "Danger zone" and click **Uninstall "GitResolve"**.

**Technical Impact**: Revocation takes effect immediately. The next time GitResolve attempts any operation on your repositories, GitHub's API returns `401 Unauthorized` or `404 Not Found`. GitResolve immediately catches this condition, raises an `access_revoked` failure, and ceases all processing on associated tasks.

---

## 11. Data Security and Technical Safeguards

GitResolve implements robust technical and organizational security measures:
- **In-Transit Encryption**: All API communications, frontend interactions, and database connections require TLS 1.2 or TLS 1.3 encryption.
- **Strict Network Controls**: Sandboxes operate without network interfaces (`--network none`). API servers enforce HTTP Strict Transport Security (HSTS) and Content Security Policies (CSP) in production.
- **Request Body Limits**: The API rejects any HTTP request payload exceeding 64 KB (`RequestBodyLimitMiddleware`), mitigating memory exhaustion attacks.
- **Rate Limiting**: Sliding-window rate limiters restrict IP-level traffic and authenticated user write operations.
- **Restricted Database Access**: Firestore security rules categorically reject client SDK connections.

---

## 12. Children's Privacy

GitResolve is an enterprise software engineering tool designed strictly for professional software developers and adult individuals. The platform is not directed toward, marketed to, or intended for use by minors or children under the age of 18 (in accordance with Section 9 of the Digital Personal Data Protection Act, 2023). We do not knowingly collect or process personal data belonging to children. If we become aware that personal data of a minor has been collected, we will take immediate steps to purge such data from our systems.

---

## 13. Policy Modifications and Notifications

We reserve the right to update or amend this Privacy Policy to reflect evolving technical implementations, legal requirements, or architectural changes. Any material changes will be indicated by updating the "Last Updated" timestamp at the top of this document. Continued use of the platform following the publication of revised terms constitutes acknowledgment of the updated practices.

---

## 14. Contact Information and Grievance Officer

For inquiries regarding this Privacy Policy, technical data handling practices, or to exercise your statutory rights under applicable privacy legislation, please contact our team:

- **Entity Name**: `[GitResolve Project Team / Sponsoring Legal Entity]` *(Placeholder)*
- **Registered Address**: `[Registered Corporate / Office Address]` *(Placeholder)*
- **Privacy Inquiry Email**: `[privacy@gitresolve.example.com]` *(Placeholder)*
- **Designated Grievance Officer (DPDPA 2023)**:
  - **Name / Designation**: `[Grievance Officer Name / Legal Officer]` *(Placeholder)*
  - **Email Address**: `[grievance-officer@gitresolve.example.com]` *(Placeholder)*
  - **Response Timeline**: Acknowledgment within 48 hours; resolution within 30 days of receipt of grievance.

