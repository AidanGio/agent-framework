# Agent Framework

A generic LangGraph + deepagents agent framework baseline. Use this as a template, then customize for your domain. (Originally derived from [langchain-ai/open-swe](https://github.com/langchain-ai/open-swe), with the coding-agent functionality stripped.)

## Status

Work in progress. The scaffold is functional — LangGraph server, Slack webhook, isolated cloud sandboxes per thread, and a small generic toolset are in place. Domain-specific tools and prompts are added per-project.

## Quickstart

Dependencies are managed with **uv**. Lint/format is **ruff** (line-length 100, target py311).

```bash
make install            # uv pip install -e .
make dev                # langgraph dev — run the LangGraph dev server
make run                # uvicorn agent.webapp:app --reload --port 8000 (webhook server only)
make test               # uv run pytest -vvv tests/
make lint               # ruff check + ruff format --diff
make format             # ruff format + ruff check --fix
```

`langgraph.json` declares the graph entrypoint as `agent.server:get_agent` and the FastAPI app as `agent.webapp:app`. `make dev` serves both together.

## Architecture

- **`agent/server.py`** — LangGraph graph factory (`get_agent`). Called per-thread; resolves or creates a sandbox, then constructs a fresh `create_deep_agent(...)` with tools and middleware.
- **`agent/webapp.py`** — custom FastAPI routes mounted alongside the LangGraph server. Slack webhooks land here; each one resolves a deterministic `thread_id` and triggers a run via the `langgraph_sdk` client.
- **`agent/middleware/`** — middleware that runs around every model call (tool-error handling, message-queue injection, status indicator refresh, sandbox circuit breaker, model fallback, step-limit notification).
- **`agent/tools/`** — the small curated toolset exposed to the agent: `fetch_url`, `http_request`, `web_search`, `slack_read_thread_messages`, `slack_thread_reply`. The deepagents built-ins (`execute`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `task`, …) are added automatically by `create_deep_agent`.
- **`agent/integrations/`** — sandbox providers: `langsmith` (default), `daytona`, `modal`, `runloop`, `local`. Provider is selected via `SANDBOX_TYPE`.

## Environment variables

**Sandbox**
- `SANDBOX_TYPE` — `langsmith` (default) | `daytona` | `modal` | `runloop` | `local`
- `LANGSMITH_API_KEY` (or `LANGSMITH_API_KEY_PROD`) — required for the `langsmith` provider
- `DEFAULT_SANDBOX_SNAPSHOT_ID` — LangSmith sandbox snapshot
- `DAYTONA_API_KEY`, `DAYTONA_SANDBOX_SNAPSHOT` — Daytona provider
- `RUNLOOP_API_KEY` — Runloop provider
- `MODAL_APP_NAME` — Modal app name (defaults to `agent`)
- `LOCAL_SANDBOX_ROOT_DIR` — root dir for the `local` provider

**Model**
- `LLM_MODEL_ID` — primary model id (default `openai:gpt-5.5`)
- `LLM_FALLBACK_MODEL_ID` — optional fallback used by `ModelFallbackMiddleware`

**Slack**
- `SLACK_BOT_TOKEN` — bot user OAuth token
- `SLACK_SIGNING_SECRET` — for webhook signature verification
- `SLACK_BOT_USER_ID`, `SLACK_BOT_USERNAME` — used to strip mentions from incoming messages

**Webapp**
- `DASHBOARD_ALLOWED_ORIGINS` — comma-separated CORS origins
- `LANGGRAPH_URL` — LangGraph server URL (used by the webhook handler when triggering runs)

**Dashboard (`ui/`)**

The optional dashboard lets users set a default model and reasoning effort. It ships
**provider-agnostic with a no-auth dev mode** — every request is the configured local
user, so it runs out of the box. To secure it, replace `get_current_user` in
`agent/dashboard/auth.py` with your auth provider (OAuth/OIDC/etc.); nothing else changes.

- `DASHBOARD_DEV_USER_ID` — local user id (default `local`); profiles are keyed by this
- `DASHBOARD_DEV_USER_NAME` — display name (default `Local User`)
- `DASHBOARD_DEV_USER_EMAIL` — local user email (default `local@example.com`)
- `CONFIGURED_ADMINS` — comma-separated admin emails (unset → the dev user is an admin)
- `VITE_DASHBOARD_API_BASE_URL` — (frontend) base URL of the dashboard backend

## License

MIT
