<div align="center">

<img src="assets/gitresolve-logo.png" alt="GitResolve" width="160" />

# GitResolve

### Investigate issues. Verify fixes. Keep humans in control.

GitResolve is an AI-assisted issue investigation and repair platform. It analyzes a
GitHub repository, proposes a bounded change, verifies it in a Docker sandbox, and
can publish a draft pull request only after human approval.

</div>

## What it does

GitResolve turns a GitHub issue or a direct bug report into a reviewable repair
workflow. It gathers repository context, develops an investigation plan, checks
whether the reported failure can be reproduced, and validates a proposed patch
before asking for approval.

The workflow has two explicit human gates:

1. **Approve the plan** before repair execution begins.
2. **Approve the exact verified diff** before GitHub publication.

Approval applies to the diff hash that was reviewed. A changed patch must be
verified and approved again. GitResolve creates draft pull requests; it does not
merge changes automatically.

## Architecture

GitResolve separates the user-facing application, control plane, asynchronous
agent worker, external integrations, and repository execution boundary. The API
coordinates requests and persists task state; repository code is run only in the
sandbox. In local development, the worker can run as an embedded thread in the
API process; production-style deployments run it separately.

### Components and responsibilities

| Component | Responsibility | Boundary |
| --- | --- | --- |
| [Frontend](frontend/README.md) | Sign-in UI, GitHub connection, repository/issue selection, task timeline, and plan/diff review | Firebase sign-in and the backend GitHub session are separate. The browser does not receive GitHub App credentials or authorize publication. |
| [API](backend/app/api/) · [backend guide](backend/README.md) | Versioned REST API, authentication, request validation, ownership/access checks, task lifecycle, and reads for task events/artifacts | Does not execute repository code. The local embedded-worker option runs agent work in a thread within the API process; production-style deployments separate the worker. |
| [Services](backend/app/services/) · [store](backend/app/services/store/) | Task transitions, policy checks, GitHub access, attachment handling, and persistence abstraction | Firestore access is isolated behind the store implementation. Repository authorization is checked against GitHub, not inferred from cached display data. |
| [Worker](backend/app/worker/) | Claims plan, execute, and publication jobs; renews leases; handles cancellation; records progress and outcomes | Runs outside API requests; it may be an embedded thread locally, while production-style deployments run a separate worker sharing durable task storage with the API. |
| [Agent runtime](backend/app/agent/) · [model router](agent/router.py) | Separate LangGraph planning and repair workflows, typed state/tools, verification strategy, and task-scoped budgets | Agent results are proposals/evidence; backend services own task statuses and enforce policy. Model calls use the shared router. |
| [GitHub integration](githubapp/) · [setup guide](docs/GITHUB_INTEGRATION.md) | OAuth/session establishment, installation-scoped repository operations, and publication | Publication revalidates the approved diff, pinned base revision, and Docker verification evidence. |
| [Sandbox](sandbox/README.md) | Acquires a pinned public repository snapshot, prepares supported dependencies, and runs commands/tests in Docker | Receives no host credentials; repository code is not executed in the API process. |

### Request, task, and worker flow

1. The frontend authenticates the user and establishes a separate GitHub session
   through the backend. The backend checks repository and task access for
   protected operations.
2. The API validates task creation, records the repository/issue or direct
   report, and dispatches a planning phase. The worker claims it transactionally
   and records a lease so concurrent workers cannot process the same task.
3. The planning workflow gathers bounded repository context and returns a
   structured plan or a request for clarification. A ready plan is stored for
   review; repair execution does not start until the user approves that plan.
4. On start, the API dispatches execution. The worker runs the bounded agent
   workflow, persists progress, and checks cancellation/lease ownership at
   operation boundaries. The workflow pins the base revision, runs baseline and
   reproduction checks, proposes a constrained diff, and verifies the regression
   and project checks through the sandbox.
5. Backend policy and evidence checks decide whether a candidate may enter
   `awaiting_approval`. The user reviews the current diff and its hash. A
   subsequent diff change invalidates that approval.
6. After approval, a separate publication phase rechecks the approved hash,
   pinned base SHA, and trusted sandbox receipt, then creates a branch, commit,
   and **draft** pull request. GitResolve does not merge it.

Tasks are the durable lifecycle record. Task events power the timeline; artifacts
hold bounded review material such as plans, diffs, and verification output.
Worker leases, checkpoints, and transactional transitions support safe
coordination and recovery. Large source files and complete, unbounded logs are
not agent state. See the [backend schema](docs/GitResolve_Backend_Schema.md) for
the data contract and lifecycle details.

### Agent and model execution

Planning and repair execution use separate LangGraph workflows: the planning
graph lives in the project-level `agent/` package, while the resumable repair
graph lives in `backend/app/agent/graph_engine/`. The worker dispatches the
appropriate graph for each task phase; publication is a separate worker phase.
The repair graph uses typed, bounded state and validated tools for repository
inspection, patch operations, and sandbox execution. During execution,
deterministic triage and verification rules select the appropriate checks and
can escalate work; the model cannot declare a fix successful by itself.

All provider calls route through the shared ModelRouter, which owns configured
Google GenAI model selection, bounded failover/retries, health handling, and
usage reporting. Checkpoints retain bounded workflow metadata and evidence
references, not raw repository contents, secrets, or full test logs. See the
[backend guide](backend/README.md) and [roadmap](docs/ROADMAP.md) for
implementation-level details and status.

### Persistence and deployment modes

The API and worker communicate through the shared store abstraction:

- **Firestore** is the durable backend for separately deployed API and worker
  processes.
- **Memory storage** is for local single-process development and tests; it is
  not shared across processes or a production persistence layer.
- A local embedded worker is available for supported development configurations.
  Production-style deployments run the worker separately from the API.

The worker requires access to the task store, configured model/GitHub services,
and the Docker daemon used by the sandbox. The API `/health` endpoint indicates
API liveness; it does not by itself prove a separate worker or sandbox host is
available. Consult the [backend deployment notes](backend/README.md) before
choosing a topology.

### Technology at a glance

| Layer | Technologies |
| --- | --- |
| Web client | Next.js, React, TypeScript |
| API and worker | Python 3.12, FastAPI, Pydantic |
| Agent workflow | LangGraph and the shared Google GenAI ModelRouter |
| Persistence | Firestore for shared deployments; in-memory store for local/test use |
| Source control | GitHub App and GitHub API |
| Repository verification | Docker, pytest, and a dedicated Streamlit AppTest profile |

For detailed setup and limits, see the [frontend](frontend/README.md),
[backend](backend/README.md), [sandbox](sandbox/README.md), and
[GitHub integration](docs/GITHUB_INTEGRATION.md) guides.

## Safety and support boundaries

- Model responses and repository contents are treated as untrusted input.
- Deterministic scope and diff checks, verification evidence, and backend approval
  gates—not model confidence—control whether a patch can be published.
- Repository commands and tests run in Docker, not in the API process. The
  execution containers have network and resource restrictions and receive no host
  credentials.
- Docker is **not** a hardened multi-tenant VM boundary. The worker has privileged
  access to the Docker daemon; use a dedicated execution host separate from the
  API and review the [sandbox guidance](sandbox/README.md) before deployment.
- The current repair and dependency-preparation path targets Python repositories
  on CPython 3.12/Linux x86-64. Streamlit has a dedicated AppTest profile; other
  supported Python frameworks use generic pytest verification. This is not
  browser-based UI testing.

These controls describe the current implementation; they are not a certification
of production readiness or a guarantee that arbitrary code is safe to execute.
See the [roadmap](docs/ROADMAP.md) and [validation notes](docs/validation/) for
implementation status and the scope of recorded tests.

## Getting started

### Prerequisites

- Python 3.12 and [`uv`](https://docs.astral.sh/uv/)
- Node.js and npm
- Docker with a Linux engine for sandbox execution

A complete connected workflow also requires configured Google model access,
Firebase Authentication, a GitHub App, and appropriate persistence/worker
configuration. Follow the [backend](backend/README.md),
[frontend](frontend/README.md), [sandbox](sandbox/README.md), and
[GitHub integration](docs/GITHUB_INTEGRATION.md) guides for setup. Do not put
secrets in frontend variables or tracked files.

### Install dependencies

From the repository root, install the single Python environment:

```shell
uv sync --group dev
```

Install frontend dependencies in another terminal:

```shell
cd frontend
npm ci
```

### Run the development services

Start the backend from the repository root:

```shell
uv run python -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

Start the frontend from `frontend/`:

```shell
npm run dev
```

The frontend is served at `http://localhost:3000`; the API health endpoint is
`http://localhost:8000/health`. These commands start the development services;
they do not configure external authentication, model access, a separate worker,
or a working sandbox. See the component guides for the required local settings
and Docker images.

## Development checks

Run Python tests from the repository root:

```shell
uv run pytest -q
```

Run frontend checks from `frontend/`:

```shell
npm run lint
npm run build
node --test tests/speed.test.mjs
```

Sandbox integration tests require a Linux Docker engine and the reviewed images.
Some integration coverage also requires Firestore emulator configuration. Read
the [sandbox README](sandbox/README.md) and
[backend README](backend/README.md) before interpreting test results.

## Documentation

### Component guides

- [Frontend README](frontend/README.md) — frontend setup, authentication, and supported UI flows
- [Backend README](backend/README.md) — API, worker, agent execution, and local development
- [Sandbox README](sandbox/README.md) — execution contract, dependency preparation, and isolation limits

### Product and architecture

- [Product requirements](docs/GitResolve_PRD.md)
- [Technical requirements and architecture](docs/GitResolve_TRD.md)
- [Application flow](docs/GitResolve_AFD.md)
- [Backend data schema](docs/GitResolve_Backend_Schema.md)
- [Roadmap](docs/ROADMAP.md)

### Integrations, operations, and validation

- [GitHub App integration](docs/GITHUB_INTEGRATION.md)
- [Data retention and deletion](docs/DATA_RETENTION_AND_DELETION.md)
- [Privacy policy](docs/PRIVACY_POLICY.md)
- [Terms of service](docs/TERMS_OF_SERVICE.md)
- [Validation reports](docs/validation/)

Design proposals and historical implementation notes are kept under
[`docs/design/`](docs/design/) and [`docs/archive/`](docs/archive/).

## Repository layout

```text
agent/       Model routing and shared agent utilities
backend/     FastAPI application, worker, services, and tests
docs/        Product, architecture, operations, and validation documentation
frontend/    Next.js web application
githubapp/   GitHub App integration
sandbox/     Docker runner and verification profiles
scripts/     Development and operational scripts
```

## License

### All rights reserved.

GitResolve is publicly available for viewing and evaluation purposes. No open-source license is granted. Unauthorized copying, modification, redistribution, or commercial use of the source code is not permitted, except as allowed by applicable law or GitHub's Terms of Service.