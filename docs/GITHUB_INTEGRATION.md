# GitHub App integration

## Scope and human gates

`GITHUB_MODE=fixture` is the default and needs no GitHub credentials. The existing
fake agent is a lifecycle fixture, not an implementation of repair. Real mode uses
Member 3's `githubapp` package through `RealGitHubGateway` and
`GitHubPullRequestCreator`. The LLM provider abstraction is unchanged.

The real agent currently performs Gemini-backed planning only. The worker pins
the real default-branch commit before planning, supplies real issue data, and
opens an inert temporary repository snapshot through `ctx.repository()`. The
snapshot path and base SHA enter graph state; the planning LLM does not yet
investigate files or generate repairs. Member 1 still owns that implementation.
Starting repair with the current LangGraph adapter returns a recorded failure;
this is intentionally not replaced with fixture output.

The final `agent.publication` graph is invoked by the separate approved PR
worker dispatch. It does not run automatically after planning or testing.
Publication requires:

1. Exact stored diff hash matches both the task and the human approval.
2. Trusted execution-bridge receipt from Docker matches the base SHA and diff.
3. The process passed, was not OOM-killed, cleanup succeeded, and pytest reported
   at least one passing test. Collection-only/help/version calls are ineligible.
4. Existing reproduction/regression evidence and bounded patch policy pass.
5. Fresh GitHub repository permission and write permission for the signed-in user.
6. The worker still owns its lease and has not been cancelled or exceeded its deadline.

No branch, Git tree, commit or PR is created before approval. Publication creates
a **draft PR**, never merges, never force-updates a branch. Retries use a
stable branch/commit identity; an existing PR is reused only after checking its
complete tree and parent against the approved patch. A failed attempt can leave
an orphan branch or unreachable Git objects; these are never deleted automatically.
A request already accepted by GitHub cannot be undone by a later cancellation.

## Configuration

Use `backend/.env.example`. Keep private keys outside the checkout or in a secret
mount. Do not copy `.env` into images.

| Variable | Purpose |
| --- | --- |
| `GITHUB_MODE` | `fixture` (default) or `real` |
| `GITHUB_APP_ID` | Numeric App ID |
| `GITHUB_APP_CLIENT_ID` | App OAuth client ID |
| `GITHUB_APP_CLIENT_SECRET` | App OAuth client secret, API process only |
| `GITHUB_APP_PRIVATE_KEY` | PEM supplied through the environment/Secret Manager |
| `GITHUB_APP_PRIVATE_KEY_PATH` | Alternative host-only PEM path |
| `GITHUB_INSTALLATION_ID` | Optional installation restriction; never bypasses user permission checks |
| `GITHUB_APP_CALLBACK_URL` | API `/api/v1/auth/github/callback` URL registered on the App |
| `FRONTEND_BASE_URL` | Existing frontend auth callback destination |
| `SESSION_JWT_SECRET` | Strong secret (at least 32 characters); real mode rejects the development default |
| `AGENT_MODE` | `langgraph` for real mode; fake repair cannot publish |

Register/install an App on selected repositories with Metadata read, Issues read,
Contents read/write and Pull requests read/write. No Actions, administration,
workflows, secrets, webhook or merge capability is needed. Webhooks are not
implemented and no webhook secret is consumed. Read operations mint read-only
installation tokens; publication narrows a write token to the selected repository.
The OAuth user token is discarded after identity/installation discovery. Each
repository operation checks fresh collaborator permission against the immutable
GitHub user ID; installation membership in Firestore alone is insufficient.

Sign in through GET `/api/v1/auth/github/login`; the existing frontend OAuth
callback handles the session fragment. Real mode disables `/auth/dev-token`.
API and separate worker processes must share Firestore. For local memory mode,
use the embedded worker. Run the trusted worker on a Docker-capable host; build
`docker build -t gitresolve-sandbox:poc sandbox` first. The worker image includes
the modules but does not configure a Docker daemon/transport. Cloud Run is for
the API, not for nested Docker repository execution.

## Checkout and sandbox boundary

The App retrieves a pinned GitHub tar archive on the host. This is a credential-free
source snapshot, not a git clone with a credential helper, hooks or `.git` folder.
Only an HTTPS redirect to exactly `codeload.github.com` is accepted. The App token
is not forwarded to that redirect. The archive's temporary authorization URL is
never stored/logged. Bounded download/extraction rejects links and unsafe paths.
Only extracted repository files and the patch enter the container. No App keys,
installation tokens, OAuth tokens, Git metadata or host credential directories are
mounted or included in its environment. Existing isolation/resource limits remain.

Agents call `ctx.execute(commit_sha=..., command=['python','-m','pytest','-q'],
patch=...)`. The bridge supplies the authorized snapshot, caps runtime by the task
budget, checks cancellation/lease ownership, and stores the execution receipt.
Agents return `ExecuteResult` with the diff and reproduction/regression evidence.
Passing pytest does not prove bug correctness; Member 1 must implement real
reproduction and repair evaluation. Raw logs may be rotated and are explicitly
not represented as complete.

## API and frontend

Existing task fields already expose `pr.number`, `pr.url`, `pr.branch`,
`creating_pr`/`completed` status, stages and sanitized failures. GitHub events
record pinning, snapshot acquisition, branch creation and PR completion. New
artifact `github_pr` has type `pr_result`, containing the PR metadata. This is an
additive API change; the frontend type union was extended. Its existing PR card
already renders the link and branch; no redesign was needed.

Auth/access/rate-limit/network failures use stable sanitized codes. Failed task
creation returns the existing API error without creating an orphan task; worker
GitHub failures become task failures. Reads retry transient errors up to three
times; a rejected installation token is refreshed once. Writes are not blindly
retried after ambiguous network responses. Rate limits fail explicitly without
sleeping past task deadlines or immediately retrying throttled requests.

## Deployment and verification

Both Dockerfiles now build from the repository root. The API includes `githubapp`
without LLM/sandbox dependencies. The worker includes the agent and sandbox modules.
Deployment scripts use `backend/deploy/cloudbuild.yaml`; `.gcloudignore` allowlists
build sources so a root-context upload cannot include local keys or environments.

From the repository root with backend dev/agent/Firestore dependencies installed:

```text
python -m pytest -c pyproject.toml backend/tests -q -ra
python -m pytest githubapp -q -ra
python -m pytest backend/tests/sandbox -q -ra
python -m pytest backend/tests/agent -q -ra
```

Set `FIRESTORE_EMULATOR_HOST=127.0.0.1:8787` for database integration tests.
Docker tests require the image and running Linux Docker engine; skipped Docker
tests do not establish isolation. From `frontend`, run `npm ci`, `npm run lint`
and `npm run build`.

`backend/tests/integration/test_github_docker_flow.py` submits a fixture-backed
task through the real API/worker, runs failing and passing tests in actual Docker,
requires human approval, and invokes publication with mocked GitHub API writes.
It verifies API and artifact PR metadata. Its deterministic repair driver exists
only in tests and is not the LangGraph repair implementation.

A real App is not configured in this review environment. Member 3 must verify
OAuth, private repository access, installation permissions and an explicitly
approved draft PR on a disposable repository before real deployment. No real
GitHub branch/commit/PR was created by these tests. Live credentials, enterprise
SSO policies and branch protection behavior remain unverified.

API semantics were checked against GitHub's [collaborator permissions](https://docs.github.com/en/rest/collaborators/collaborators#get-repository-permissions-for-a-user),
[archive downloads](https://docs.github.com/en/rest/repos/contents#download-a-repository-archive-tar),
and [Git trees](https://docs.github.com/en/rest/git/trees#create-a-tree) documentation.
