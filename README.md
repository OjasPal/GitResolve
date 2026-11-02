# GitResolve

GitResolve is an AI-powered coding-agent project designed to investigate GitHub issues, identify likely causes, propose fixes, validate changes in isolated sandboxes, and prepare pull requests for human review.

## Multi-Model Provider Architecture & Intelligent Routing

GitResolve features an enterprise-grade multi-model routing architecture that cleanly separates **transient technical provider failover** from **specialist reasoning escalation**.

### Model Hierarchy

| Priority | Model | Model ID | Provider | Purpose |
|---|---|---|---|---|
| **Main** | Claude Sonnet 5.5 | `claude-sonnet-5-5` | Anthropic on Google Cloud | Normal agent execution (planning & standard repair) |
| **Fallback 1** | Gemini 4 Argon | `gemini-4-argon` | Google Agent Platform (Vertex AI) | First technical/infrastructure failover |
| **Fallback 2** | Gemini 3.8 Flash | `gemini-3.8-flash` | Google Agent Platform (Vertex AI) | Second technical/infrastructure failover |
| **Specialist Escalation** | Claude Opus 5.5 | `claude-opus-5-5` | Anthropic on Google Cloud | High-difficulty reasoning, cross-file bugs, repair escalation |

### Technical Failover vs. Reasoning Escalation

It is critical to distinguish these two mechanisms:

1. **Technical / Provider Failover (Automatic Infrastructure Resilience)**
   - Claude Sonnet 5.5 is the primary model.
   - If Sonnet encounters genuine transient infrastructure errors (HTTP 429 rate limiting, HTTP 500/502/503/504 errors, network connection drops, timeouts), the router retries with exponential backoff and jitter up to `LLM_MAX_PROVIDER_RETRIES`.
   - If retries are exhausted, the router fails over to **Gemini 4 Argon**, and if that also encounters infrastructure outages, to **Gemini 3.8 Flash**.
   - If all providers fail, a clean `AllProvidersFailedError` is returned.
   - **Never triggered by reasoning or workflow issues**: Incorrect patches, test suite failures, or missing information do NOT cause technical failovers.
   - **Non-retryable errors fail fast**: Authentication (401/403) or configuration errors raise immediate fatal errors without blind failovers.

2. **Specialist Reasoning Escalation (Workflow-Driven Intelligence)**
   - Claude Opus 5.5 is **never** part of the technical failover chain. It is never invoked merely because a primary model had a network outage.
   - Opus is explicitly invoked by the agent reasoning workflow when a problem is unusually difficult:
     - The repair loop reaches repeated failure thresholds (e.g. patch failed test suite).
     - Cross-file impact or complex multi-module dependencies are detected.
     - Structured evaluation signals indicate low confidence.
   - Escalation is strictly bounded by `LLM_MAX_OPUS_ESCALATIONS` (default `1`) to prevent uncontrolled cost or infinite loops.

### Authentication via Application Default Credentials (ADC)

GitResolve uses Google Cloud Application Default Credentials (ADC) across all models:
- **Anthropic models**: Hosted via Google Cloud's managed partner-model integration using `anthropic[vertex]` and `AnthropicVertex`.
- **Gemini models**: Hosted via Google GenAI SDK (`google-genai`) with `enterprise=True`.

No AI Studio API keys, direct Anthropic API keys, or raw service-account JSON key files are used.

#### Local Development Setup

Authenticate your local environment using the Google Cloud SDK:

```bash
gcloud auth application-default login
```

Ensure your GCP project has the Vertex AI API enabled and has permission to access Claude partner models and Gemini models.

---

## Setup & Configuration

### Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [gcloud CLI](https://cloud.google.com/sdk/docs/install) (for ADC authentication)

### Install Dependencies

```bash
uv sync
```

### Environment Configuration

Copy `.env.example` to `.env` in the repository root and customize as needed:

```bash
cp .env.example .env
```

Key environment variables:

```dotenv
# Google Cloud / Agent Platform
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=global
GOOGLE_GENAI_USE_ENTERPRISE=true

# Anthropic on Google Cloud
ANTHROPIC_VERTEX_PROJECT_ID=your-gcp-project-id
ANTHROPIC_VERTEX_REGION=global

# Model Routing Hierarchy
LLM_PRIMARY_PROVIDER=anthropic
LLM_PRIMARY_MODEL=claude-sonnet-5-5

LLM_FALLBACK_1_PROVIDER=google
LLM_FALLBACK_1_MODEL=gemini-4-argon

LLM_FALLBACK_2_PROVIDER=google
LLM_FALLBACK_2_MODEL=gemini-3.8-flash

LLM_ESCALATION_PROVIDER=anthropic
LLM_ESCALATION_MODEL=claude-opus-5-5

# Routing Behavior
LLM_MAX_PROVIDER_RETRIES=2
LLM_ENABLE_FAILOVER=true
LLM_ENABLE_OPUS_ESCALATION=true
LLM_MAX_OPUS_ESCALATIONS=1
```

### How to Switch Models

Models and providers can be switched entirely through environment variables without code modifications. For example:
- To change the primary model, set `LLM_PRIMARY_MODEL` and `LLM_PRIMARY_PROVIDER`.
- To adjust fallback models, update `LLM_FALLBACK_1_MODEL` or `LLM_FALLBACK_2_MODEL`.
- To disable failover or escalation, set `LLM_ENABLE_FAILOVER=false` or `LLM_ENABLE_OPUS_ESCALATION=false`.

---

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
- `agent/providers.py` — Normalized provider clients for `AnthropicVertex` and `GoogleGenAI` (`enterprise=True`), error classification (retryable vs fatal).
- `agent/router.py` — Central model router implementing bounded retry backoff, provider failover chains, and specialist reasoning escalation.
- `agent/state.py` — LangGraph shared state including routing and observability metadata.
- `agent/graph.py` — LangGraph planning workflow with backward-compatible adapters.
- `agent/repair.py` — Bounded repair execution workflow evaluating patch generation, test outcomes, and specialist reasoning escalation triggers.
- `agent/analysis_schema.py` — Structured Pydantic schema for issue analysis.
- `agent/test_router.py` — Comprehensive unit test suite verifying all 12 provider failover, retry, and escalation scenarios.
- `agent/test_graph.py` — Graph state, workflow planning, and routing metadata tests.
