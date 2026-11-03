# Data Retention and Deletion Policy for GitResolve

> **LEGAL NOTICE AND DRAFT STATUS**  
> *This document is a working administrative and technical draft prepared for GitResolve and is subject to formal review and revision by qualified legal counsel. This document does not constitute formal legal certification, compliance validation, or binding legal advice. Applicable legal frameworks considered in the preparation of this draft include the Digital Personal Data Protection Act, 2023 (DPDPA 2023) of India, applicable rules and commencement provisions, and enterprise information security standards. Organizations deploying GitResolve must consult qualified legal counsel to adapt this document to their specific legal jurisdiction, corporate entity, and deployment configuration.*

---

## 1. Scope, Purpose, and Governing Principles

This Data Retention and Deletion Policy defines the data lifecycle management practices of GitResolve. The policy details the categories of data stored across our architecture, the specific storage mechanisms and infrastructure utilized, retention timeframes, automatic expiration schedules, and deletion procedures.

GitResolve adheres to the core data governance principles articulated in the Digital Personal Data Protection Act, 2023 (DPDPA 2023):
1. **Data Minimization (Section 6)**: Collecting and retaining only digital personal data that is strictly necessary for the specified purpose of automated software defect analysis and repair.
2. **Purpose Limitation (Section 4)**: Processing data solely to execute the repair workflows explicitly initiated or authorized by the developer.
3. **Storage Limitation (Section 8(7))**: Ensuring data is retained only for the duration necessary to satisfy the purpose of processing, and systematically purged or rendered inaccessible thereafter.

---

## 2. Categories of Stored Data

GitResolve processes and stores six distinct functional categories of technical and personal data:

| Category | Data Items Included | Data Classification |
| :--- | :--- | :--- |
| **A. Identity & Authentication** | GitHub numerical user ID (`githubUserId`), GitHub login handle (`githubLogin`), avatar image URL, authorized installation IDs (`installationIds`), active task identifier (`activeTaskId`), timestamps of account creation and last login; Firebase Auth UID, email address, display name. | Personal Data |
| **B. Repository & Issue Snapshots** | Target repository full name (`owner/repo`), repository ID, default branch, visibility, issue number, issue title, issue body (capped at 20,000 characters), issue labels, issue comments (up to 10 comments, 2,000 characters each), and pinned base commit SHA (`baseSha`). | Technical & Operational Data |
| **C. Task State & Metadata** | Task ID (`tsk_...`), user association, dispatch state, execution phase, timeout budgets, failure classifications, review gate states, and cryptographic diff digests (`finalDiffHash`). | Operational Data |
| **D. Operational Events & Logs** | Chronological audit logs (`seq`, `ts`, `stage`, `type`, `level`, `message`, and sanitized scalar metadata). | System Telemetry & Audit |
| **E. Execution Artifacts** | Investigation plans, reproduction test scripts (`repro_test`), iteration patches (`attempt_*_diff`), test execution logs (`attempt_*_tests`), candidate patches (`final_diff`), final test reports (`final_tests`), and pull request metadata (`github_pr`). | Technical & Code Data |
| **F. Transient Execution Data** | Temporary repository tarball archives, extracted file trees, and isolated Docker container filesystems. | Ephemeral Workspace Data |

---

## 3. Storage Locations, Systems, and Access Controls

GitResolve maintains data across several strictly partitioned storage systems:

```
+-----------------------------------------------------------------------------------------+
|                                     GITRESOLVE SYSTEM                                   |
|                                                                                         |
|  +--------------------+    +--------------------+    +-------------------------------+  |
|  |   Client Browser   |    |    Backend API     |    |        Worker Process         |  |
|  |                    |    |                    |    |                               |  |
|  | - Session JWT      |    | - Session Minting  |    | - Lease Heartbeats            |  |
|  | - Firebase Token   |    | - Rate Limiter (IP)|    | - Ephemeral Host Workspaces   |  |
|  | - OAuth Cookie     |    | - Body Limiter     |    |   (/tmp/gitresolve_*)         |  |
|  +--------------------+    +--------------------+    +-------------------------------+  |
+-----------------------------------|----------------------------------|------------------+
                                    |                                  |
                                    v                                  v
+-----------------------------------------------------------------------------------------+
|                               PERSISTENCE & CLOUD PLATFORM                              |
|                                                                                         |
|  +--------------------------------+  +-----------------------------------------------+  |
|  | Google Cloud Identity Platform |  | Google Cloud Firestore (Native Mode)          |  |
|  |                                |  |                                               |  |
|  | - Frontend Firebase Auth       |  | - users/{userId}                              |  |
|  | - Google / Password identities |  | - tasks/{taskId}                              |  |
|  |                                |  | - tasks/{taskId}/events/{seq} (Subcollection) |  |
|  |                                |  | - tasks/{taskId}/artifacts/{id} (Subcoll.)    |  |
|  |                                |  |                                               |  |
|  |                                |  | * Access Rule: Client SDKs Denied (IAM Only)  |  |
|  +--------------------------------+  +-----------------------------------------------+  |
+-----------------------------------------------------------------------------------------+
```

### 3.1. Google Cloud Firestore (Native Mode)
- **Role**: Primary persistent database for production environments (`STORE_BACKEND=firestore`).
- **Security Control**: Client-side SDK access is entirely blocked via `firestore.rules`. Only the backend server SDK authenticated through Google Cloud IAM service account credentials can execute reads and writes.
- **Hosted Region**: Configured under the team's enterprise Google Cloud Project (`GCP_PROJECT`, `FIRESTORE_DATABASE`).

### 3.2. Google Cloud Identity Platform / Firebase Auth
- **Role**: User identity directory for frontend authentication.
- **Security Control**: Governed by Google Cloud enterprise security standards. Passwords (where utilized) are hashed using Google's secure scrypt implementation.

### 3.3. Worker Host Filesystem (Ephemeral)
- **Role**: Execution staging for repository snapshot tarballs and patch preparation.
- **Location**: Created via `tempfile.mkdtemp(prefix="gitresolve_")` on the worker host machine.
- **Security Control**: Accessible only by the local worker OS process; never mounted with host credentials or cloud keys.

### 3.4. Docker Container Sandbox (Ephemeral)
- **Role**: Execution boundary for defect reproduction and test execution.
- **Storage**: Container root filesystem is mounted strictly **read-only** (`--read-only`); writable areas exist solely as volatile in-memory RAM disks (`tmpfs` mounted at `/tmp` and `/workspace`).
- **Security Control**: Non-root execution (`10001:10001`), capabilities dropped (`--cap-drop ALL`), network disabled (`--network none`).

### 3.5. Client-Side Browser Storage
- **Role**: Client session persistence.
- **Storage**:
  - `localStorage`: Stores `gitresolve_session_owner` (Firebase UID string).
  - Memory: Backend Bearer JWT is held in client memory or HTTP Authorization headers.
  - Cookies: `gitresolve_oauth_state` (HttpOnly, Secure in production, Lax SameSite, 10-minute lifetime).

---

## 4. Retention Schedules and Time-to-Live (TTL)

GitResolve enforces strict retention schedules designed to ensure data is not maintained beyond its operational utility:

| Data Type / Entity | Storage System | Established Retention Period | Expiration & Purge Mechanism |
| :--- | :--- | :--- | :--- |
| **Primary Task Documents** (`tasks/{taskId}`) | Cloud Firestore | **90 Days** from task creation (`task_retention_days = 90`) | Computed `expiresAt = createdAt + 90 days`. Filtered out on read; physical purge via Firestore TTL. |
| **Event Timeline Logs** (`tasks/{taskId}/events/{seq}`) | Cloud Firestore | **30 Days** from task creation (`subdoc_retention_days = 30`) | Computed `expiresAt = createdAt + 30 days`. Filtered out on read; physical purge via Firestore TTL. |
| **Execution Artifacts** (`tasks/{taskId}/artifacts/{id}`) | Cloud Firestore | **30 Days** from task creation (`subdoc_retention_days = 30`) | Computed `expiresAt = createdAt + 30 days`. Filtered out on read; physical purge via Firestore TTL. |
| **User Profile Documents** (`users/{userId}`) | Cloud Firestore | Indefinite until manual account deletion request | Preserved to maintain active task ownership and authorized installation mappings. |
| **Backend Session JWTs** | Client / Memory | **60 Minutes** (`jwt_ttl_minutes = 60`) | Automatic cryptographic expiration (`exp` claim); rejected by backend middleware upon expiry. |
| **GitHub OAuth CSRF Cookie** | Client Cookie | **10 Minutes** (600 seconds) | Automatic browser expiration via `max_age = 600`. Explicitly deleted upon callback completion. |
| **GitHub App Installation Tokens** | Worker Memory | **Under 60 Minutes** | Ephemeral, host-memory only. Minted on-the-fly and automatically expired by GitHub. Never stored in database. |
| **GitHub OAuth User Access Token** | API Memory | **0 Seconds (Discarded Immediately)** | Used exclusively during authentication callback to query user profile; discarded without saving. |
| **Worker Host Temp Workspaces** | Host Filesystem | **Duration of execution (Max 300 seconds)** | Deleted immediately in worker execution `finally` block via `shutil.rmtree`. |
| **Docker Sandbox Containers** | Docker Engine | **Duration of execution (Max 300 seconds)** | Forcibly stopped and removed immediately in `finally` block via `docker rm -f`. |

---

## 5. Deletion Mechanics and Implementation Status

To maintain rigorous architectural fidelity, GitResolve distinguishes between implemented deletion mechanisms and features planned for subsequent releases.

### 5.1. Implemented Deletion Capabilities
1. **Read-Time Logical Expiry Filtering (Implemented)**:
   The backend service (`app.services.task_service`) actively filters all read operations against document expiration timestamps. When querying tasks, events, or artifacts:
   ```python
   # Evaluated on every read query
   if task.get("expiresAt") is not None and task["expiresAt"] <= clock():
       raise not_found()
   ```
   Any task older than 90 days or any event/artifact older than 30 days is rendered permanently inaccessible to API callers and returns a `404 Not Found`.
2. **Ephemeral Workspace and Container Cleanup (Implemented)**:
   In `sandbox.runner.run_sandbox`, cleanup is guaranteed through defensive `finally` blocks:
   - The execution container is forcibly removed via `docker rm -f`.
   - The temporary host directory (`/tmp/gitresolve_*`) is unlinked and deleted via `shutil.rmtree`.
   - Cleanup failures immediately transition task state to `failed_infra` with `cleanup_failed` alarms.
3. **Task Cancellation (Implemented)**:
   Users may issue `POST /tasks/{id}/cancel` to immediately halt execution. Active workers terminate processing at the next bounded checkpoint and release the user's active task slot.

### 5.2. Partially Implemented: Physical Database Purging
- **Firestore TTL Policies**: Document schemas define explicit `expiresAt` timestamp fields (`tasks`, `events`, `artifacts`). In production Google Cloud environments, automated physical erasure requires configuring Google Cloud Firestore TTL policies on the `expiresAt` field via Google Cloud Console or `gcloud firestore fields ttls update`.
- **In-App Purge Daemon**: No standalone database-sweeper cron service is executed within the application process itself. Physical purging relies on Google Cloud's managed infrastructure.

### 5.3. Planned Deletion Capabilities (Not Implemented)
- **In-App Self-Service Account Deletion**: GitResolve does **not** currently implement an automated `DELETE /api/v1/auth/me` endpoint or UI button to delete user profiles.
- **In-App On-Demand Task Deletion**: GitResolve does **not** currently provide a button or API to manually purge historical task records before their 90-day expiration window.
- **Automated Webhook Uninstallation Purge**: GitResolve does not consume GitHub App uninstallation webhooks; repository access is revoked at the GitHub API boundary rather than via background synchronization.

---

## 6. Account Deletion and Manual Deletion Procedures

In compliance with Section 12 of the Digital Personal Data Protection Act, 2023 (Right to Correction and Erasure of Personal Data), users possess the right to obtain the erasure of their personal data.

### 6.1. Current Manual Erasure Procedure
Because an automated self-service deletion workflow is currently classified as **Planned**, users wishing to permanently delete their account and associated data may initiate a manual deletion request:

1. **Submission**: Submit an email request from your registered developer email to:
   - **Data Protection Contact**: `[privacy@gitresolve.example.com]` *(Placeholder)*
   - **Subject Line**: "Request for Personal Data Erasure - [Your GitHub Username]"
2. **Identity Verification**: To prevent unauthorized or fraudulent deletion requests, our administrators will verify your identity by requesting a signed confirmation via your registered GitHub account or email.
3. **Execution by Database Administrators**:
   Upon verification, a system administrator executes manual database deletion scripts:
   - Purges the user record located at `users/{userId}` in Google Cloud Firestore;
   - Queries and deletes all associated task documents in `tasks/` where `userId == targetUserId`;
   - Recursively deletes all corresponding `events` and `artifacts` subcollections;
   - Deletes the associated user profile in Google Cloud Identity Platform (Firebase Auth).
4. **Confirmation**: A formal written confirmation of completion is issued within thirty (30) days of receipt.

### 6.2. Roadmap for Automated Account Deletion
GitResolve has scheduled the implementation of an end-to-end self-service account deletion system in an upcoming milestone:
- **API Endpoint**: Implementation of `DELETE /api/v1/auth/me` requiring re-authentication;
- **Cascade Deletion**: Background worker job to transactionally wipe user records, task histories, event logs, and stored artifacts;
- **UI Integration**: Addition of a "Delete Account" button within the frontend `SettingsPage` (`frontend/src/app/settings/page.tsx`).

---

## 7. GitHub Account Disconnection Procedure

Users may revoke GitResolve's access to their repositories at any time without administrative intervention.

### 7.1. User-Initiated Disconnection Steps
1. Navigate to **GitHub.com** and open **Settings** from your profile dropdown.
2. In the left sidebar, click **Applications** and select the **Installed GitHub Apps** tab.
3. Locate **GitResolve** and click **Configure**.
4. To revoke access to specific repositories, update the **Repository access** list.
5. To completely disconnect GitResolve, navigate to the **Danger zone** and click **Uninstall**.

### 7.2. Technical Consequence of Disconnection
- **Immediate Effect**: Revocation is immediate on GitHub's API servers.
- **Backend Enforcement**: When a user or background worker next attempts to access repository metadata, issue bodies, or commit SHAs, GitHub rejects the request with HTTP `401 Unauthorized` or `404 Not Found`.
- **Fail-Safe Abort**: The GitResolve gateway catches this response, maps it to `FailureCode.ACCESS_REVOKED`, immediately terminates any in-progress planning or repair tasks, and prevents further interaction with the repository.

---

## 8. Third-Party AI Provider Retention Considerations

GitResolve transmits issue descriptions, error logs, and code patches to artificial intelligence providers hosted on **Google Cloud Vertex AI** (Anthropic Claude and Google Gemini).

### 8.1. Data Handling by AI Processors
- **Stateless API Invocations**: Prompts sent to Vertex AI endpoints are processed statelessly in-memory to generate completions (investigation plans, diff patches, PR summaries).
- **Enterprise Commitments & Policy Qualifications**: In accordance with standard Google Cloud Vertex AI enterprise documentation and Anthropic on Vertex AI terms:
  - Subject to the customer's enterprise Google Cloud agreement and applicable data processing terms, customer prompts and code submitted via enterprise Vertex AI endpoints are not utilized by default to train, retrain, or improve Google or Anthropic public foundation models.
  - Prompts and generated completions are not logged into public or shared corpora.
  - Data retention on Google Cloud infrastructure is governed by Google Cloud's operational data processing terms and any active abuse-monitoring or security audit logging configurations (typically transient or up to 30 days where operational logging is enabled).
  - Deploying organizations must review their specific Google Cloud Project settings, enterprise agreements, and Model Garden / Vertex AI partner agreements to confirm their organization's data retention and compliance posture.

---

## 9. Backup Systems, Disaster Recovery, and Legal Records

### 9.1. Managed Cloud Backups
- If automated disaster recovery backups are enabled on the production Google Cloud Firestore database, snapshot backups are maintained within encrypted Google Cloud Storage buckets.
- **Backup Retention Window**: Backup snapshots are retained for a rolling window of between 7 and 35 days (dependent on Cloud Firestore Point-in-Time Recovery [PITR] configuration).
- **Eventual Erasure**: Data deleted from the live Firestore database will persist in existing snapshot archives until those backup snapshots naturally expire and are overwritten according to the rolling schedule.

### 9.2. Preservation for Legal Obligations
Notwithstanding user erasure requests, GitResolve may retain minimal technical logs or transaction identifiers where strictly required to:
- Comply with applicable statutory obligations under Indian law (including directives issued by the Indian Computer Emergency Response Team [CERT-In] under Section 70B of the Information Technology Act, 2000 regarding system logs);
- Investigate, detect, or defend against active cyber attacks, fraud, or security breaches; or
- Establish, exercise, or defend legal claims in judicial or arbitral proceedings.

---

## 10. Summary Matrix of Retention and Deletion Status

| Data Classification | Storage Location | Retention Lifetime | Automatic Deletion Status | Manual Deletion Mechanism |
| :--- | :--- | :--- | :--- | :--- |
| **User Profile** | Firestore (`users/`) | Indefinite | Not Implemented | Written request to Privacy Officer |
| **Tasks** | Firestore (`tasks/`) | 90 Days | Read-filtered (Implemented); Physical TTL (Partially Implemented) | Written request to Privacy Officer |
| **Events & Logs** | Firestore (`.../events/`) | 30 Days | Read-filtered (Implemented); Physical TTL (Partially Implemented) | Written request to Privacy Officer |
| **Diffs & Artifacts** | Firestore (`.../artifacts/`) | 30 Days | Read-filtered (Implemented); Physical TTL (Partially Implemented) | Written request to Privacy Officer |
| **Session JWT** | Browser / Memory | 60 Minutes | Cryptographic Expiration (Implemented) | Client sign-out (`signOutUser`) |
| **OAuth State** | Browser Cookie | 10 Minutes | Browser Cookie Deletion (Implemented) | Automatic upon login completion |
| **Installation Tokens** | Host Memory | < 60 Minutes | In-Memory Expiration (Implemented) | Automatic process garbage collection |
| **Host Workspaces** | Host `/tmp` | < 300 Seconds | Active Process Deletion (Implemented) | Automated `finally` cleanup block |
| **Docker Sandboxes** | Host Docker Daemon | < 300 Seconds | Active Container Deletion (Implemented) | Automated `finally` cleanup block |

---

## 11. Contact and Administrative Information

To submit data retention inquiries, report concerns regarding data storage, or submit a formal data erasure request under the Digital Personal Data Protection Act, 2023, please contact:

- **Entity Name**: `[GitResolve Project Team / Sponsoring Legal Entity]` *(Placeholder)*
- **Registered Address**: `[Registered Corporate / Office Address]` *(Placeholder)*
- **Data Protection Officer / Grievance Officer**: `[Name / Title of Grievance Officer]` *(Placeholder)*
- **Data Erasure & Privacy Email**: `[privacy@gitresolve.example.com]` *(Placeholder)*
- **Official Grievance Redressal Timeline**: Acknowledgment within 48 hours; full resolution within thirty (30) days.

