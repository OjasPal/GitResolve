# GitResolve frontend foundation

Run `npm ci` and `npm run dev` in this directory. Open http://localhost:3000.
Start the backend on port 8000 with local authentication enabled and CORS allowing
http://localhost:3000. The default API is http://localhost:8000/api/v1.
`NEXT_PUBLIC_API_BASE_URL` overrides it; health defaults to the same origin's
/health endpoint and can be overridden with `NEXT_PUBLIC_HEALTH_URL`.

The current UI supports local development authentication, repository/issue
selection, task creation, status polling, events, artifacts and human gates.
Production builds disable development login unless explicitly opted into with
`NEXT_PUBLIC_ENABLE_DEV_AUTH=true` at build time. Use that override only for
local smoke tests against a local backend, never as hosted authentication.
GitHub App sign-in and publishing remain unfinished. Real LangGraph currently
provides planning; repair execution still belongs to Member 1. A backend using
AGENT_MODE=fake produces simulated lifecycle results, not real fixes or PRs.

Task polling restarts after human actions. Approval requires the displayed,
complete diff artifact hash to match the task's current finalDiffHash. Backend
ownership, policy and approval checks remain authoritative.

Validation: `npm run lint`, `npm run build`, and
`npm audit --omit=dev --audit-level=high`. The current lint run has warnings
for unused imports and image optimization, but no errors. Do not suppress lint
rules or apply forced dependency upgrades to hide issues.
