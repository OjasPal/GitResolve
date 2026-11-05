# GitResolve

An autonomous GitHub repair agent with two human approval gates. Investigate a request, reproduce the issue, verify a bounded fix in Docker, then review the exact patch before creating a draft pull request. Nothing merges automatically.

## Model routing

| Role | Model | Provider |
| --- | --- | --- |
| Primary | `gemini-3.8-flash` | Google Cloud |
| Technical fallback | `gemini-3.6-flash` | Google Cloud |
| Reasoning specialist | `gemini-4-argon` | Google Cloud |

Technical timeouts fail over immediately. A primary rate limit with a server-requested delay of at most two seconds permits one retry. Other technical failures switch directly to the backup; backup failure enters controlled recovery with no further model calls. A successful but malformed response or an inconclusive evaluation can request one Argon reasoning review, unless recent provider failures have opened the health circuit. Argon technical failure or another inconclusive result enters recovery. SDK retries are disabled; six repair attempts remain a separate budget. The provider-health circuit is process-local, shared by callers for the same project and location. A failed test or an incorrect patch goes through the repair loop, not provider failover. Authentication and configuration failures stop with a clear explanation.

Use Application Default Credentials with an eligible Google Cloud project. Run `gcloud auth application-default login` locally. Project identification alone does not grant access or credits.

## Local setup

Use Python 3.12 and the single root environment:

```powershell
uv sync --group dev
uv run python -m uvicorn app.main:app --app-dir backend --reload --port 8000
```

Root `.env` configures model routing. `backend/.env` configures the API, GitHub App and worker. `frontend/.env.local` configures the frontend. Copy the corresponding examples; never commit credentials.

Review conversations are available only at the two approval gates. At the plan gate, ask questions, request a revised plan, or edit your original request to trigger a fresh investigation. At the fix gate, ask about the recorded evidence or request a refinement that must pass verification again. Editing the original request at this stage requires a new workflow. Conversation calls count against the run's existing model budget; refinements preserve its repair-attempt limits and invalidate earlier approvals.

```dotenv
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=global
GOOGLE_GENAI_USE_ENTERPRISE=true
LLM_PRIMARY_PROVIDER=google
LLM_PRIMARY_MODEL=gemini-3.8-flash
LLM_FALLBACK_1_PROVIDER=google
LLM_FALLBACK_1_MODEL=gemini-3.6-flash
LLM_ENABLE_FAILOVER=true
LLM_MAX_PROVIDER_RETRIES=1
LLM_REQUEST_TIMEOUT_SECONDS=45
LLM_SPECIALIST_MODEL=gemini-4-argon
LLM_SPECIALIST_TIMEOUT_SECONDS=60
```

## Running Tests

All unit tests use mocked provider responses and zero live paid inference credits:

```bash
uv run pytest -v
```

To run only the agent test suite:

```bash
uv run pytest agent -v
```

To run backend integration tests:

```bash
uv run --directory backend pytest tests/integration/test_langgraph_adapter.py -v
```

---

## Agent Architecture

- `agent/config.py` — Centralized configuration, model hierarchies, retry parameters, and token pricing catalog.
- `agent/observability.py` — Token usage tracking (input, output, cached), cost estimation, and credential sanitization.
- `agent/providers.py` — Normalized provider clients for `GoogleGenAI` (`enterprise=True`), error classification (retryable vs fatal).
- `agent/router.py` — Central model router implementing bounded retry backoff, provider failover chains,.
- `agent/state.py` — LangGraph shared state including routing and observability metadata.
- `agent/graph.py` — LangGraph planning workflow with backward-compatible adapters.
- `agent/repair.py` — Bounded repair execution workflow evaluating patch generation, test outcomes, triggers.
- `agent/analysis_schema.py` — Structured Pydantic schema for issue analysis.
- `agent/test_router.py` — Comprehensive unit test suite verifying primary/fallback routing and bounded retry behavior.
- `agent/test_graph.py` — Graph state, workflow planning, and routing metadata tests.
