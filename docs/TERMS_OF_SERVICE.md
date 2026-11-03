# Terms of Service for GitResolve

> **LEGAL NOTICE AND DRAFT STATUS**  
> *This document is a working administrative and technical draft prepared for GitResolve and is subject to formal review and revision by qualified legal counsel. This document does not constitute formal legal certification, compliance validation, or binding legal advice. Applicable legal frameworks considered in the preparation of this draft include the Information Technology Act, 2000, the Indian Contract Act, 1872, the Digital Personal Data Protection Act, 2023 of India, and standard international software licensing principles. Organizations deploying GitResolve must consult qualified legal counsel to adapt this document to their specific legal jurisdiction, corporate entity, and deployment configuration.*

---

## 1. Acceptance of Terms and Eligibility

By accessing, browsing, registering an account with, connecting a repository to, or using the GitResolve software platform (collectively, the "Platform", "Service", "we", "us", or "our"), you ("User", "you", or "Developer") enter into a legally binding agreement and agree to be bound by these Terms of Service ("Terms").

### 1.1. Eligibility Criteria
To use GitResolve, you represent and warrant that:
1. You are at least eighteen (18) years of age and possess the legal capacity to form a binding contract under applicable law (including the Indian Contract Act, 1872);
2. If you are entering into these Terms on behalf of a company, organization, or legal entity, you possess the full legal authority and necessary corporate approvals to bind that entity to these Terms;
3. You possess all requisite rights, licenses, and permissions from the owners or administrators of any GitHub repository that you connect to or authorize GitResolve to access; and
4. Your use of GitResolve does not violate any applicable domestic or international laws, export regulations, or contractual obligations.

If you do not agree to these Terms in their entirety, you must not access or use the Platform.

---

## 2. Description of the Service and Operational Reality

GitResolve is an automated, AI-assisted development tooling system designed to assist software engineers in investigating, reproducing, and repairing software issues reported on GitHub repositories.

### 2.1. System Workflow and Operational Bounds
The Platform operates through an asynchronous task lifecycle:
1. **Intake & Issue Snapshots**: The Platform reads public or private repository issue reports and discussion comments via the GitHub App integration.
2. **Investigation Planning**: An AI planning graph (LangGraph) invokes large language models to construct an investigation plan and summarize the problem.
3. **Plan Approval Gate (Gate 1)**: Execution pauses until an authorized human user inspects and approves the plan.
4. **Isolated Sandbox Execution**: A containerized Docker runner executes automated test reproduction and patch evaluation within an isolated, non-networked container.
5. **Diff Approval Gate (Gate 2)**: Execution pauses until an authorized human user inspects the generated unified diff patch and cryptographically approves its SHA-256 digest.
6. **Publication**: Upon cryptographic approval, GitResolve creates a dedicated git branch and publishes a **draft pull request** on the user's repository.

### 2.2. Architectural Reality & Modes of Operation
Users acknowledge that GitResolve may be deployed in different operating environments:
- **Production Mode**: Connects to live GitHub App endpoints, Google Cloud Firestore, and Google Cloud Vertex AI (Claude and Gemini models).
- **Local / Test Fixture Mode**: In local or testing environments (`GITHUB_MODE=fixture`, `AGENT_MODE=fake`), GitHub interactions and agent repair loops operate against deterministic local mock fixtures and an in-memory data store. Features verified in fixture mode do not constitute representations of live cloud behavior.

---

## 3. User Responsibilities and Account Security

As a user of the Platform, you agree to:
1. **Authentication Security**: Maintain the confidentiality of your authentication credentials, including GitHub credentials, Firebase credentials, session tokens, and passwords. You are solely responsible for all activities occurring under your account.
2. **Immediate Notification**: Notify us immediately upon discovering any unauthorized access, security incident, credential leak, or breach of your account.
3. **Accurate Information**: Provide true, accurate, and current information when registering and managing your account.
4. **Authorized Repository Access**: Connect only repositories that you own or for which you have received express, written authorization from the repository owner to permit automated code inspection, test execution, and pull request generation.

---

## 4. GitHub Account and Repository Permissions

### 4.1. Grant of Limited License and Access
By installing the GitResolve GitHub App on your account or organization, you grant GitResolve a limited, non-exclusive, revocable license to access, read, download, process, analyze, compile, and execute your repository source code, issues, and metadata solely for the purpose of providing the Service.

### 4.2. Repository Permissions and Collaborator Verification
- The GitResolve GitHub App requires repository permissions for **Metadata (read)**, **Issues (read)**, **Contents (read/write for PR branches)**, and **Pull Requests (read/write for draft PRs)**.
- GitResolve will never attempt to force-push, alter default branches, delete branches, or merge pull requests.
- GitResolve verifies personal collaborator access before executing any task. You acknowledge that if your GitHub permissions on a repository are revoked or modified, GitResolve will immediately refuse or abort task execution.

### 4.3. Disconnection of GitHub Access
You maintain the right to revoke GitResolve's access to your GitHub account or specific repositories at any time through your GitHub account settings (**Settings > Applications > Installed GitHub Apps > GitResolve**). Revocation terminates GitResolve's ability to access your repositories immediately.

---

## 5. Acceptable Use and Prohibited Activities

You agree that you will not use GitResolve to engage in any prohibited activities. You shall not:

1. **Malicious Code Generation**: Instruct the AI or use the Platform to synthesize, compile, test, or distribute computer viruses, worms, Trojan horses, rootkits, ransomware, spyware, backdoors, keyloggers, or other malicious software.
2. **Unauthorized Security Exploitation**: Use the Platform to attack, exploit, conduct unauthorized vulnerability scans, or probe third-party repositories, computer networks, or systems.
3. **Sandbox Escape and Resource Abuse**: Attempt to circumvent, tamper with, bypass, or compromise the Docker sandbox environment, process isolation barriers, user permission boundaries, read-only filesystem locks, or memory/CPU budgets.
4. **Denial of Service**: Overwhelm, flood, launch denial-of-service (DoS/DDoS) attacks against, or interfere with the normal operation, rate limiters, or responsiveness of the GitResolve API, worker processes, or third-party providers.
5. **Intellectual Property Infringement**: Process or submit repository code, proprietary information, or trade secrets in violation of third-party copyrights, patents, trademarks, non-disclosure agreements, or proprietary software licenses.
6. **Reverse Engineering**: Decompile, reverse engineer, disassemble, decrypt, or attempt to derive the underlying source code, algorithms, or private architecture of the proprietary components of the Platform, except to the extent permitted by applicable open-source licenses.
7. **Bypassing Access Controls**: Forge HTTP headers, spoof IP addresses, impersonate other users or GitHub organizations, or utilize automated scripts to generate unauthorized tasks.
8. **Violation of Law**: Use the Service in violation of any applicable local, state, national, or international law, including the Information Technology Act, 2000 and the Digital Personal Data Protection Act, 2023 of India.

---

## 6. AI-Generated Code Limitations and Inherent Risks

GitResolve utilizes artificial intelligence models provided by third parties (Anthropic Claude and Google Gemini on Google Cloud Vertex AI) to generate software analysis, test suites, and code modifications.

### 6.1. Probabilistic and Experimental Nature
**YOU EXPRESSLY ACKNOWLEDGE AND AGREE THAT:**
1. AI models operate probabilistically and may generate incorrect, defective, incomplete, suboptimal, obsolete, or hallucinatory code patches.
2. Passing unit tests or reproduction tests within the isolated sandbox environment **does not guarantee** that a proposed patch is mathematically correct, functionally complete, free of security vulnerabilities, or suitable for production deployment.
3. The AI model may misunderstand the root cause of an issue, misinterpret comments, or produce changes that introduce regressions, security flaws, performance degradation, or data corruption in your software.
4. AI-generated code proposals are advisory suggestions intended solely to assist human software developers and must never be treated as authoritative software engineering solutions.

---

## 7. Mandatory Human Review and Approval Responsibilities

GitResolve is fundamentally designed around an uncompromised **Human-in-the-Loop** safety architecture.

### 7.1. Sole User Responsibility for Review
**YOU BEAR THE EXCLUSIVE, FINAL RESPONSIBILITY FOR THOROUGHLY REVIEWING, VALIDATING, TESTING, AND APPROVING ANY INVESTIGATION PLAN AND CODE PATCH GENERATED BY GITRESOLVE.**

### 7.2. Cryptographic Approval Boundary
- When a task reaches the `awaiting_approval` state, GitResolve presents the exact proposed unified diff patch along with its cryptographic SHA-256 digest (`finalDiffHash`).
- By submitting an approval payload, you certify that you have conducted a professional, line-by-line engineering review of the diff and have independently determined that the patch is safe, functional, and compliant with your standards.
- GitResolve provides no warranty or representation that an approved patch will work outside the isolated sandbox.

### 7.3. Pull Request Review and Merging
GitResolve publishes only **draft pull requests** to dedicated branches. GitResolve never merges code into your main, master, or production branches. You, as the repository administrator, retain absolute discretion and responsibility regarding whether to review, test, accept, or merge a draft pull request on GitHub.

---

## 8. Docker Sandbox and Execution Environment Limitations

### 8.1. Sandbox Isolation Constraints
Reproduction tests and patches are executed within temporary, isolated Docker containers:
- Containers run with `--network none` (no internet connectivity). Tests that require external internet access, third-party cloud services, external databases, or remote package downloads will fail in the sandbox.
- Containers are limited to 2 CPU cores, 2GB of RAM, and default execution timeouts (300 seconds). Tasks requiring substantial hardware resources or lengthy compilation steps will terminate with out-of-memory (`oom_killed`) or timeout errors.
- Filesystems are mounted read-only with limited temporary memory storage.

### 8.2. Non-Equivalence to Production Environments
The sandbox environment uses standardized Linux container images (`gitresolve-sandbox:poc` or `gitresolve-sandbox:streamlit`). The execution environment may differ significantly from your specific deployment target, operating system, hardware architecture, kernel configuration, or dependency versions. Successful execution in the sandbox does not imply compatibility with your production infrastructure.

---

## 9. Intellectual Property and Code Ownership

### 9.1. User Ownership of Code and Repositories
You retain all right, title, ownership, and intellectual property rights in and to your repositories, source code, issue content, and modifications approved and merged by you. GitResolve claims no ownership, copyright, or proprietary interest in your source code or repository data.

### 9.2. GitResolve Intellectual Property
GitResolve and its licensors retain all right, title, and interest in and to the Platform, including its user interface, architecture, backend API, worker orchestration, routing algorithms, documentation, logos, trademarks, and proprietary software.

### 9.3. Rights in AI-Generated Output
To the maximum extent permitted by applicable law, GitResolve assigns to you any proprietary claim or copyright interest it may hold in the specific unified diffs and investigation plans generated for your tasks. You acknowledge that AI-generated content may be subject to legal limitations regarding copyrightability under the laws of various jurisdictions.

---

## 10. Service Availability, Modifications, and Quotas

### 10.1. "As Available" Service
GitResolve is provided on an "AS IS" and "AS AVAILABLE" basis. We do not warrant that the Service will be uninterrupted, timely, secure, error-free, or compatible with all third-party software, GitHub features, or programming languages.

### 10.2. Usage Limits and Quotas
We enforce technical quotas and limits to maintain platform stability and protect system resources:
- Maximum tasks per day (default: 10 tasks/day per user);
- Single concurrent active task per user (`activeTaskId`);
- Maximum repair attempts per task (default: 3 attempts);
- Maximum model invocations per task (default: 25 calls);
- Maximum diff size (default: 5 files, 150 lines);
- Task execution timeouts (default: 20 minutes total task timeout; 300 seconds sandbox timeout).

We reserve the right to modify these limits or throttle accounts exceeding standard operational thresholds.

---

## 11. Disclaimer of Warranties

**TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW:**

1. **NO WARRANTIES**: GITRESOLVE, ITS DIRECTORS, EMPLOYEES, AFFILIATES, AGENTS, AND LICENSORS EXPRESSLY DISCLAIM ALL WARRANTIES OF ANY KIND, WHETHER EXPRESS, IMPLIED, STATUTORY, OR OTHERWISE, INCLUDING BUT NOT LIMITED TO THE IMPLIED WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, TITLE, ACCURACY, NON-INFRINGEMENT, AND QUIET ENJOYMENT.
2. **NO GUARANTEE OF DEFECT REPAIR**: WE DO NOT WARRANT OR GUARANTEE THAT GITRESOLVE WILL IDENTIFY, REPRODUCE, OR RESOLVE ANY SOFTWARE BUG, DEFECT, OR SECURITY VULNERABILITY IN YOUR REPOSITORY, OR THAT PROPOSED PATCHES WILL BE ERROR-FREE OR PRODUCTION-READY.
3. **THIRD-PARTY PROVIDERS**: WE MAKE NO WARRANTIES REGARDING THE ACCURACY, UPTIME, AVAILABILITY, OR INTEGRITY OF THIRD-PARTY SERVICES, INCLUDING GITHUB, GOOGLE CLOUD VERTEX AI, ANTHROPIC, OR FIREBASE.

---

## 12. Limitation of Liability

**TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW (INCLUDING THE INDIAN CONTRACT ACT, 1872 AND THE INFORMATION TECHNOLOGY ACT, 2000):**

1. **EXCLUSION OF CONSEQUENTIAL DAMAGES**: IN NO EVENT SHALL GITRESOLVE, ITS DIRECTORS, EMPLOYEES, AGENTS, OR LICENSORS BE LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, PUNITIVE, OR EXEMPLARY DAMAGES, INCLUDING BUT NOT LIMITED TO DAMAGES FOR LOSS OF PROFITS, REVENUE, GOODWILL, USE, DATA, PRODUCTION DOWNTIME, CORRUPTED CODEBASES, FAILED DEPLOYMENTS, SYSTEM CRASHES, REPOSITORY LOSS, OR OTHER INTANGIBLE LOSSES, ARISING OUT OF OR IN CONNECTION WITH YOUR USE OF, OR INABILITY TO USE, THE SERVICE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.
2. **CAP ON MONETARY LIABILITY**: UNDER NO CIRCUMSTANCES SHALL THE AGGREGATE TOTAL LIABILITY OF GITRESOLVE ARISING OUT OF OR RELATING TO THESE TERMS OR THE USE OF THE SERVICE EXCEED THE GREATER OF:
   - THE TOTAL AMOUNT PAID BY YOU TO GITRESOLVE FOR USE OF THE SERVICE DURING THE THREE (3) MONTHS IMMEDIATELY PRECEDING THE EVENT GIVING RISE TO LIABILITY; OR
   - ONE THOUSAND INDIAN RUPEES (INR 1,000).
3. **ESSENTIAL BASIS OF BARGAIN**: YOU ACKNOWLEDGE AND AGREE THAT THE DISCLAIMERS OF WARRANTIES AND LIMITATIONS OF LIABILITY CONTAINED HEREIN CONSTITUTE AN ESSENTIAL BASIS OF THE BARGAIN BETWEEN YOU AND GITRESOLVE, WITHOUT WHICH THE PLATFORM WOULD NOT BE MADE AVAILABLE.

---

## 13. Indemnification

You agree to defend, indemnify, and hold harmless GitResolve, its developers, operators, affiliates, directors, officers, and agents from and against any claims, liabilities, damages, losses, costs, and expenses (including reasonable attorneys' fees and legal costs) arising out of or in any way connected with:
1. Your access to or use of the Platform;
2. Your violation of these Terms;
3. Your submission, approval, or merging of AI-generated code that infringes third-party intellectual property or causes software damage, security vulnerabilities, or system failures;
4. Your violation of any third-party right, including copyright, proprietary right, or privacy right; or
5. Any dispute between you and any third party regarding repository ownership, authorization, or code contributions.

---

## 14. Suspension and Termination

### 14.1. Suspension or Termination by GitResolve
We reserve the right, in our sole discretion and without prior notice or liability, to immediately suspend, restrict, or terminate your access to GitResolve if:
- You violate, breach, or threaten to breach any provision of these Terms;
- Your activity threatens the security, stability, or integrity of the Platform, sandbox hosts, or other users;
- Required by law, law enforcement, or competent judicial authority;
- You engage in abusive, unauthorized, or excessive consumption of computational or AI provider resources.

### 14.2. Effect of Termination
Upon termination of your account or access, your right to use the Platform immediately ceases. Sections 5, 6, 7, 9, 11, 12, 13, 15, and 16 shall survive any termination or expiration of these Terms.

---

## 15. Governing Law and Dispute Handling

### 15.1. Applicable Law
These Terms and any dispute, controversy, or claim arising out of or relating to them or the Platform shall be governed by, construed, and enforced in accordance with the laws of **India**, without regard to its conflict of law principles.

### 15.2. Jurisdiction
Subject to Section 15.3, the parties agree that the competent courts situated in `[City / State, India]` *(Placeholder - specify jurisdiction)* shall have exclusive jurisdiction over any legal proceedings arising out of or in connection with these Terms.

### 15.3. Dispute Resolution and Amicable Settlement
In the event of any dispute or difference arising between the parties regarding the interpretation, implementation, or breach of these Terms, the parties shall first endeavor to resolve the dispute amicably through good-faith mutual negotiations. If the dispute is not settled within thirty (30) days from the date of written notice by either party, either party may initiate legal proceedings in the designated courts.

---

## 16. General Provisions

1. **Entire Agreement**: These Terms, together with the Privacy Policy and Data Retention Policy, constitute the entire and exclusive agreement between you and GitResolve regarding the subject matter herein.
2. **Severability**: If any provision of these Terms is determined by a court of competent jurisdiction to be invalid, illegal, or unenforceable, that provision shall be modified to the minimum extent necessary to make it valid and enforceable, and the remaining provisions shall remain in full force and effect.
3. **No Waiver**: No failure or delay by GitResolve in exercising any right, power, or remedy under these Terms shall operate as a waiver thereof.
4. **Assignment**: You may not assign or transfer these Terms, in whole or in part, without our prior written consent. We may assign our rights and obligations under these Terms without restriction.

---

## 17. Contact Information

For any questions, legal notices, or feedback concerning these Terms of Service, please reach out to our team:

- **Entity Name**: `[GitResolve Project Team / Sponsoring Legal Entity]` *(Placeholder)*
- **Registered Address**: `[Registered Corporate / Office Address]` *(Placeholder)*
- **Legal Inquiry Email**: `[legal@gitresolve.example.com]` *(Placeholder)*
- **Support Contact**: `[support@gitresolve.example.com]` *(Placeholder)*

