# Customization Guide

The agent is assembled in a single function — `get_agent()` in `agent/server.py` — which constructs a fresh `deepagents.create_deep_agent(...)` per thread with a model, sandbox backend, tool list, and middleware stack. Most customization is done by editing one of those three lists.

```python
# agent/server.py — abbreviated
return create_deep_agent(
    model=make_model(model_id, **model_kwargs),
    system_prompt=construct_system_prompt(working_dir=work_dir),
    tools=[
        http_request,
        fetch_url,
        web_search,
        slack_read_thread_messages,
        slack_thread_reply,
    ],
    backend=backend_factory,
    middleware=[
        SanitizeToolInputsMiddleware(),
        ModelCallLimitMiddleware(run_limit=MODEL_CALL_RECURSION_LIMIT, exit_behavior="end"),
        ToolErrorMiddleware(),
        check_message_queue_before_model,
        SlackAssistantStatusMiddleware(),
        ensure_no_empty_msg,
        notify_step_limit_reached,
        SandboxCircuitBreakerMiddleware(),
        *fallback_middleware,
    ],
).with_config(config)
```

---

## Adding a tool

Tools live in `agent/tools/` and are flat-imported via `agent/tools/__init__.py`. Built-in deepagents tools (`read_file`, `execute`, `glob`, `grep`, `task` for subagent spawning, ...) are added by `create_deep_agent` itself — don't duplicate them.

1. **Create the module** at `agent/tools/my_tool.py`. The function's docstring is the tool description seen by the model — write it clearly.

   ```python
   # agent/tools/my_tool.py
   def my_tool(query: str) -> dict:
       """One-line description of what the tool does, in plain English.

       Args:
           query: ...

       Returns:
           ...
       """
       ...
   ```

2. **Export it** from `agent/tools/__init__.py`:

   ```python
   from .my_tool import my_tool

   __all__ = [..., "my_tool"]
   ```

3. **Register it** in the `tools=[...]` list inside `get_agent()` in `agent/server.py`.

To remove a tool, drop it from both the `__init__.py` exports and the `tools=[...]` list.

### Conditional tools

You can vary the toolset per-thread by inspecting `config["configurable"]` in `get_agent()` before building the list.

---

## Adding middleware

Middleware lives in `agent/middleware/` and runs around every model call. **Order matters** — earlier entries run earlier in the wrap chain.

1. **Create the module** at `agent/middleware/my_middleware.py`. Use the `@before_model` / `@after_agent` decorators from `langchain.agents.middleware`, or subclass one of the middleware base classes. See [LangChain middleware docs](https://python.langchain.com/docs/concepts/agents/#middleware).

   ```python
   from langchain.agents.middleware import AgentState, after_agent
   from langgraph.runtime import Runtime

   @after_agent
   async def my_hook(state: AgentState, runtime: Runtime):
       """Runs once after the agent finishes."""
       ...
   ```

2. **Export it** from `agent/middleware/__init__.py`.

3. **Append it** to the `middleware=[...]` list in `get_agent()`. Put it where it makes sense relative to the others (e.g. error handlers wrap inner middleware; before-model hooks that modify input run before guards like `ensure_no_empty_msg`).

---

## Adding a sandbox provider

Each thread gets its own sandbox. The provider is selected via the `SANDBOX_TYPE` env var; the factory registry lives in `agent/utils/sandbox.py`. Existing providers: `langsmith` (default), `daytona`, `modal`, `runloop`, `local`.

1. **Create an integration file** at `agent/integrations/my_provider.py` with a factory function:

   ```python
   def create_my_provider_sandbox(sandbox_id: str | None = None):
       """Create or reconnect to a sandbox.

       Args:
           sandbox_id: Optional existing sandbox id to reconnect to.
                       If None, create a new sandbox.

       Returns:
           An object implementing SandboxBackendProtocol from deepagents.
       """
       ...
   ```

2. **Register it** in `agent/utils/sandbox.py`:

   ```python
   from agent.integrations.my_provider import create_my_provider_sandbox

   SANDBOX_FACTORIES = {
       ...,
       "my_provider": create_my_provider_sandbox,
   }
   ```

3. **Select it** by setting `SANDBOX_TYPE=my_provider` in your environment.

### Implementing the backend

The factory must return an object implementing `SandboxBackendProtocol` from `deepagents`. The protocol requires file operations (`ls`, `read`, `write`, `edit`, `glob`, `grep`), `execute(command, timeout=None) -> ExecuteResponse`, and an `id` property.

The easiest path is to extend `BaseSandbox` from `deepagents.backends.sandbox` — it implements all file operations on top of `execute()`, so you only implement the shell layer:

```python
from deepagents.backends.sandbox import BaseSandbox
from deepagents.backends.protocol import ExecuteResponse

class MySandbox(BaseSandbox):
    def __init__(self, connection):
        self._conn = connection

    @property
    def id(self) -> str:
        return self._conn.id

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        result = self._conn.run(command, timeout=timeout or 300)
        return ExecuteResponse(
            output=result.stdout + result.stderr,
            exit_code=result.exit_code,
            truncated=False,
        )
```

See `agent/integrations/langsmith.py` and the other existing integrations for full examples.
