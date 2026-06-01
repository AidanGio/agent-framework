# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

This repo is a generic LangGraph + deepagents agent framework baseline. It runs as a LangGraph app: each thread spawns its own isolated cloud sandbox, and the agent is invoked from Slack. The codebase is intentionally generic — domain-specific tools and prompts are added per-project by editing `agent/tools/`, `agent/server.py`, and `default_prompt.md`.

## Commands

Dependencies are managed with **uv**. Tests use pytest (`asyncio_mode = "auto"`). Lint/format is **ruff** (line-length 100, target py311).

```bash
make install            # uv pip install -e .
make dev                # langgraph dev — run the LangGraph dev server (graph defined in langgraph.json)
make run                # uvicorn agent.webapp:app --reload --port 8000 (webhook server only)
make test               # uv run pytest -vvv tests/
make test TEST_FILE=tests/test_something.py             # single test file
uv run pytest -vvv tests/test_something.py::test_name   # single test
make lint               # ruff check + ruff format --diff
make format             # ruff format + ruff check --fix
```

`langgraph.json` declares the graph entrypoint as `agent.server:get_agent` and the FastAPI app as `agent.webapp:app`. Both are served together by `langgraph dev`.

## Architecture

### Two entrypoints, one process

- **`agent/server.py` → `get_agent(config)`** — the LangGraph graph factory. Called per-thread. Gets-or-creates the sandbox for the thread, then constructs a fresh `create_deep_agent(...)` with the full tool list and middleware stack. The agent itself is stateless — all per-thread state lives in the sandbox + thread metadata.
- **`agent/webapp.py`** — custom FastAPI routes mounted alongside the LangGraph server. This is where the Slack webhook lands. The handler resolves a deterministic `thread_id` (so follow-up messages route to the same agent run) then triggers/streams a run via the `langgraph_sdk` client.

### Sandbox lifecycle (the tricky part)

`SANDBOX_BACKENDS` is an in-process dict keyed by `thread_id`. Thread metadata persists `sandbox_id` across processes. `ensure_sandbox_for_thread` handles four cases:

1. Sandbox cached in memory → ping it (`echo ok`); recreate on `SandboxClientError`.
2. Metadata says `__creating__` and no cache → poll until ready (`_wait_for_sandbox_id`).
3. No sandbox at all → create one, set `__creating__` sentinel, then real id.
4. Metadata has an id but no cache → reconnect; fall back to recreate on failure.

Provider is selected via the `SANDBOX_TYPE` env var (`langsmith` default, plus `daytona`, `modal`, `runloop`, `local`). Factory is `agent/utils/sandbox.py:create_sandbox`. For `SANDBOX_TYPE=langsmith`, `_start_langsmith_sandbox_if_needed` ensures the sandbox is in `running`/`ready` state before use.

### Middleware stack (order matters)

Configured in `get_agent`, runs around every model call:

1. `SanitizeToolInputsMiddleware` — strips/normalizes tool-input payloads before they reach the model.
2. `ModelCallLimitMiddleware` — caps model calls per run; exits cleanly when hit.
3. `ToolErrorMiddleware` — catches tool exceptions.
4. `check_message_queue_before_model` — pulls Slack messages that arrived mid-run from the thread queue and injects them as user messages before the next LLM call. This is what makes "message the agent while it's working" work.
5. `SlackAssistantStatusMiddleware` — refreshes the Slack assistant typing/status indicator periodically during a run.
6. `ensure_no_empty_msg` — guards against empty assistant messages that some providers reject.
7. `notify_step_limit_reached` — after-agent hook that posts a Slack reply when the agent hits the step limit, so the user gets a clear signal instead of silence.
8. `SandboxCircuitBreakerMiddleware` — trips if sandbox calls start failing repeatedly.
9. `ModelFallbackMiddleware` (conditional) — appended when `LLM_FALLBACK_MODEL_ID` (or an inferred fallback) differs from the primary model.

### Tools

All tools live in `agent/tools/` and are flat-imported via `agent/tools/__init__.py`. The current set is intentionally small: `fetch_url`, `http_request`, `web_search`, `slack_read_thread_messages`, `slack_thread_reply`. Built-in deepagents tools (`execute`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `task` for subagent spawning, …) are added by `create_deep_agent` itself; don't duplicate them.

### Webhooks

Only Slack is wired up. Signatures are verified in `agent/utils/slack.py:verify_slack_signature` using `SLACK_SIGNING_SECRET`. Thread-id derivation lives alongside the Slack helpers so the same Slack thread routes back to the same running agent.

## Conventions

- Tests are unit-only by default (`tests/`). Integration tests would go under `tests/integration_tests/` (currently empty — `make integration_tests` no-ops if missing).
- New sandbox providers: add a module under `agent/integrations/` and wire it into `agent/utils/sandbox.py:create_sandbox`.
- New tools: add to `agent/tools/`, export from `agent/tools/__init__.py`, add to the `tools=[...]` list in `server.py:get_agent`.
- New middleware: add to `agent/middleware/`, export from `agent/middleware/__init__.py`, add to the `middleware=[...]` list in `server.py:get_agent` — order is significant.
