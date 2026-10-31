# Backend foundation

Use Python 3.12. From backend: `uv pip install -e '.[dev,agent,firestore]'`.

## Frontend-free local prototype

Set ENVIRONMENT=local, STORE_BACKEND=memory, EMBEDDED_WORKER=true,
AGENT_MODE=langgraph, GEMINI_API_KEY, then run
`uvicorn app.main:app --host 127.0.0.1 --port 8000`.
The local-only embedded thread shares the memory store. Separate API/worker
processes require STORE_BACKEND=firestore and configured ADC or an emulator.

1. POST `/api/v1/auth/dev-token` with `{"userId":"1","githubLogin":"local"}`.
2. Use the returned accessToken as a Bearer token.
3. POST `/api/v1/tasks` with `{"repoId":"1001_2002","issueNumber":1}`.
4. Poll GET `/api/v1/tasks/{id}`, `/events`, and `/artifacts`.
5. Real LangGraph planning ends at awaiting_start. POST `/start` currently
   produces a clear failed_infra result because agent repair is not implemented.

The repository/issue gateway is a local fixture, not GitHub integration.
AGENT_MODE=fake is explicitly a lifecycle test fixture; it is not a real repair.
Production continues to fail closed without real GitHub providers.

## Images and validation

API: `docker build -f backend/Dockerfile backend`.
Worker: `docker build -f backend/Worker.Dockerfile .` from repository root.
The worker image includes the agent but does not provide a Docker daemon.
Use the host worker for sandbox execution until Docker transport is configured.
Never mount the Docker socket in a repository execution container.

Run `python -m pytest -q -ra` here; set FIRESTORE_EMULATOR_HOST to include
Firestore integration tests. No production deployment is implied by unit tests.

Usage counters are cumulative and monotonic. Agent code must checkpoint between
operations, reserve/report model calls, and pass remaining time/cancellation to
execution tools. The Gemini client has a 60-second request timeout. Python
threads cannot forcibly terminate arbitrary agent code; only bounded tools are
supported. Expired tasks cannot publish results through the normal worker path.
