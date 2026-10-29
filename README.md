# GitResolve

GitResolve is an AI-powered coding-agent project designed to investigate GitHub issues, identify likely causes, propose fixes, validate changes, and prepare pull requests for human review.

## Current Agent Prototype

The initial agent component uses LangGraph to orchestrate issue analysis and Google's Gemini API to generate a structured investigation plan.

### Current capabilities

- Accepts GitHub issue details as input.
- Generates a problem summary and investigation steps using Gemini.
- Validates structured model output with Pydantic.
- Handles simulated LLM failures in automated tests.
- Uses LangGraph state to track workflow progress.

### Current limitations

- Does not fetch GitHub issues automatically.
- Does not inspect repository files yet.
- Does not reproduce bugs, modify code, or run tests against a target repository.
- Does not create branches or pull requests.
- The current `awaiting_start` state is not yet a real approval pause.

## Setup

Requires a compatible Python installation.

```bash
python -m venv .venv
```

Activate the environment, then install dependencies:

```bash
python -m pip install -r requirements.txt
```

Create a `.env` file in the repository root:

```dotenv
GEMINI_API_KEY=your_api_key_here
GEMINI_MODEL=gemini-3.8-flash
```

Never commit `.env` or expose your API key.

## Run tests

```bash
python -m pytest agent -v
```

The automated tests use a mocked LLM response, so they do not require a live Gemini API call.

## Agent structure

- `agent/state.py` — shared workflow state.
- `agent/graph.py` — LangGraph workflow and routing.
- `agent/analysis_schema.py` — validated issue-analysis output.
- `agent/llm.py` — Gemini API integration.
- `agent/test_graph.py` — graph behavior and failure tests.
- `agent/test_analysis_schema.py` — structured-output schema test.

## Project documentation

See [`docs/`](docs/) for the product requirements, technical design, backend schema, architecture/design document, and implementation plan.

## Security

Repository code must eventually execute only in an isolated sandbox. Never execute untrusted repository code inside the API server or agent worker. Pull-request creation must require explicit human approval of the proposed changes.

## Status

Early development. The agent is a prototype and is not yet integrated with the backend, execution sandbox, or frontend.