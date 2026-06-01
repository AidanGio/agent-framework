# Installation Guide

This guide walks you through setting up the agent framework locally. It's a LangGraph + deepagents app with a FastAPI webhook server for Slack.

## Prerequisites

- **Python 3.11 – 3.13**
- [uv](https://docs.astral.sh/uv/) package manager
- The `langgraph` command-line tool (`pip install langgraph-cli`)
- [ngrok](https://ngrok.com/) (for local development — exposes the Slack webhook to the internet)

## 1. Clone and install

```bash
git clone <this-project-url> agent-framework
cd agent-framework
make install            # uv pip install -e .
```

## 2. Start ngrok

```bash
ngrok http 2024 --url https://some-url-you-configure.ngrok.dev
```

Passing `--url` keeps the subdomain stable so you don't have to update Slack each time you restart. Keep this terminal running; use a second terminal for the remaining steps.

## 3. Create a Slack app

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**.
2. Under the Slack admin UI's permissions panel, add the bot scopes you need (typical: `app_mentions:read`, `chat:write`, `channels:history`, `groups:history`, `im:history`, `mpim:history`, `reactions:write`, `users:read`).
3. Install the app to your workspace and copy the **Bot User Token** (`xoxb-...`).
4. Under **Basic Information → App Credentials**, copy the **Signing Secret**.
5. Under **Event Subscriptions**:
   - Enable events.
   - Set **Request URL** to `https://<your-ngrok-url>/webhooks/slack`.
   - Subscribe to bot events: `app_mention`, `message.im`, `message.mpim` (add others as your use case requires).

## 4. Choose a sandbox provider

Each agent run executes shell commands in an isolated sandbox. Pick a provider via `SANDBOX_TYPE`:

| `SANDBOX_TYPE` | Notes | Required env |
|---|---|---|
| `langsmith` (default) | LangSmith cloud sandboxes. Requires a pre-built snapshot. | `LANGSMITH_API_KEY` (or `LANGSMITH_API_KEY_PROD`), `DEFAULT_SANDBOX_SNAPSHOT_ID` |
| `daytona` | Daytona-managed sandboxes. | `DAYTONA_API_KEY` (optional `DAYTONA_SANDBOX_SNAPSHOT`) |
| `modal` | Modal Functions backend. | Modal credentials configured via `modal token set`; optional `MODAL_APP_NAME` |
| `runloop` | Runloop devboxes. | `RUNLOOP_API_KEY` |
| `local` | Runs commands directly on the host. **No isolation — development only.** | Optional `LOCAL_SANDBOX_ROOT_DIR` |

See `agent/integrations/` for each provider's implementation and `agent/utils/sandbox.py` for the factory registry.

## 5. Environment variables

Create a `.env` in the project root. Only fill in the sections relevant to the sandbox provider and LLM provider you use.

```bash
# === LLM (configured in agent/utils/model.py) ===
ANTHROPIC_API_KEY=""
OPENAI_API_KEY=""
LLM_MODEL_ID=""                  # provider:model, e.g. "anthropic:claude-sonnet-4-6"

# === Slack ===
SLACK_BOT_TOKEN=""               # xoxb-...
SLACK_SIGNING_SECRET=""          # Required — webhooks are rejected without it

# === Sandbox ===
SANDBOX_TYPE="langsmith"         # langsmith | daytona | modal | runloop | local

# LangSmith sandbox (when SANDBOX_TYPE=langsmith)
LANGSMITH_API_KEY=""
DEFAULT_SANDBOX_SNAPSHOT_ID=""   # Required for langsmith; build via the LangSmith UI or scripts/
DEFAULT_SANDBOX_SNAPSHOT_FS_CAPACITY_BYTES=""  # Optional, default 32 GiB
DEFAULT_SANDBOX_VCPUS=""                       # Optional, default 4
DEFAULT_SANDBOX_MEM_BYTES=""                   # Optional, default 15 GiB
DEFAULT_SANDBOX_IDLE_TTL_SECONDS=""            # Optional, default 600; 0 disables
DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS=""   # Optional, default 86400; 0 disables

# Other providers (set only if applicable)
DAYTONA_API_KEY=""
DAYTONA_SANDBOX_SNAPSHOT=""
RUNLOOP_API_KEY=""
MODAL_APP_NAME=""
LOCAL_SANDBOX_ROOT_DIR=""

# === Optional: LangSmith tracing ===
LANGCHAIN_TRACING_V2="true"
LANGCHAIN_PROJECT=""

# === Optional: web search tool ===
EXA_API_KEY=""
```

## 6. Run the server

With ngrok still running from step 2:

```bash
make dev                 # uv run langgraph dev
```

The LangGraph dev server runs on `http://localhost:2024` and serves both the graph (`agent.server:get_agent`) and the FastAPI app (`agent.webapp:app`). Slack events land at `POST /webhooks/slack`; health check is `GET /health`.

For webhook-only development (no graph), `make run` boots just the FastAPI app under uvicorn on port 8000.

## 7. Verify it works

In a channel where the bot is invited, mention it: `@<your-bot-name> hello`. You should see a reply in the thread. Check ngrok's inspector at `http://localhost:4040` to confirm the event was delivered.

## Troubleshooting

- **Webhook 401s**: `SLACK_SIGNING_SECRET` is unset or wrong.
- **No reply**: bot not invited to the channel, or events not subscribed in the Slack app config.
- **Sandbox boot fails on langsmith**: `DEFAULT_SANDBOX_SNAPSHOT_ID` is unset, or the snapshot UUID doesn't exist in your LangSmith workspace.
- **Local provider doing strange things**: it executes on your host. Switch to a real provider for anything beyond smoke tests.
