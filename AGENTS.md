# AGENTS.md

Contributor guide for this LangGraph + deepagents agent framework baseline. Slack is the trigger surface. Domain-specific tools and prompts are project-specific and not part of the baseline.

> **Status: in flux.** This codebase was forked from a coding-agent framework. All coding-agent specifics have been stripped from the Python code. What remains is the LangGraph + deepagents + FastAPI scaffold, sandbox lifecycle management, and a handful of generic tools.

## Layout

- `agent/` — the LangGraph application.
  - `agent/server.py` — `get_agent(config)` graph factory; assembles tools, middleware, sandbox, and model.
  - `agent/webapp.py` — FastAPI routes (Slack webhooks) mounted alongside the LangGraph server.
  - `agent/tools/` — generic tools (`fetch_url`, `http_request`, `web_search`, `slack_read_thread_messages`, `slack_thread_reply`). Flat-imported via `agent/tools/__init__.py`.
  - `agent/middleware/` — middleware modules; exported from `agent/middleware/__init__.py`.
  - `agent/integrations/` — sandbox provider implementations (`langsmith`, `daytona`, `modal`, `runloop`, `local`). Wired into `agent/utils/sandbox.py:create_sandbox` via `SANDBOX_FACTORIES`.
  - `agent/utils/` — sandbox factory, Slack helpers, model factory, etc.
- `tests/` — unit tests (pytest). Integration tests would live under `tests/integration_tests/` (currently empty).
- `scripts/` — one-off helper scripts.
- `ui/` — frontend assets (separate from the Python app).
- `langgraph.json` — declares `agent.server:get_agent` as the graph and `agent.webapp:app` as the HTTP app.

## Commands

Dependencies are managed with **uv**. Tests use pytest (`asyncio_mode = "auto"`). Lint/format is **ruff** (line-length 100, target py311).

```bash
make install            # uv pip install -e .
make dev                # langgraph dev — serves the graph + FastAPI together
make run                # uvicorn agent.webapp:app --reload --port 8000 (webhook server only)
make test               # uv run pytest -vvv tests/
make test TEST_FILE=tests/test_foo.py                  # single test file
uv run pytest -vvv tests/test_foo.py::test_name        # single test
make lint               # ruff check + ruff format --diff
make format             # ruff format + ruff check --fix
```

## Conventions

- **Python 3.11+**; ruff line-length 100, target py311.
- **Tests are unit-only by default** under `tests/`. pytest `asyncio_mode = "auto"` — no need to decorate async tests.
- **New tools**: add a module under `agent/tools/`, export from `agent/tools/__init__.py`, and add to the `tools=[...]` list in `agent/server.py:get_agent`.
- **New middleware**: add to `agent/middleware/`, export from `agent/middleware/__init__.py`, and append to the `middleware=[...]` list in `agent/server.py:get_agent` — order is significant.
- **New sandbox providers**: add a module under `agent/integrations/` and register it in `agent/utils/sandbox.py:SANDBOX_FACTORIES`. See `CUSTOMIZATION.md`.
- Keep the tool set small and curated — prefer extending an existing tool over adding a near-duplicate.
