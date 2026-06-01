"""Main entry point and CLI loop for the agent."""
# ruff: noqa: E402

# Suppress deprecation warnings from langchain_core (e.g., Pydantic V1 on Python 3.14+)
import logging
import os
import warnings
from typing import Any

logger = logging.getLogger(__name__)

from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel
from langgraph_sdk import get_client

warnings.filterwarnings("ignore", module="langchain_core._api.deprecation")

import asyncio

# Suppress Pydantic v1 compatibility warnings from langchain on Python 3.14+
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)

from deepagents import create_deep_agent
from deepagents.backends import LangSmithSandbox
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.agents.middleware import ModelCallLimitMiddleware
from langsmith.sandbox import SandboxClientError

from .dashboard.agent_overrides import (
    load_profile,
    normalize_profile_overrides,
    resolve_profile_login,
)
from .middleware import (
    ModelFallbackMiddleware,
    SandboxCircuitBreakerMiddleware,
    SanitizeToolInputsMiddleware,
    SlackAssistantStatusMiddleware,
    ToolErrorMiddleware,
    check_message_queue_before_model,
    ensure_no_empty_msg,
    notify_step_limit_reached,
)
from .prompt import construct_system_prompt
from .tools import (
    fetch_url,
    http_request,
    slack_read_thread_messages,
    slack_thread_reply,
    web_search,
)
from .utils.model import (
    AnthropicEffort,
    AnthropicThinking,
    ModelKwargs,
    OpenAIReasoning,
    fallback_model_id_for,
    make_model,
)
from .utils.sandbox import create_sandbox
from .utils.sandbox_paths import aresolve_sandbox_work_dir

client = get_client()

SANDBOX_CREATING = "__creating__"
SANDBOX_CREATION_TIMEOUT = 180
SANDBOX_POLL_INTERVAL = 1.0

from .utils.sandbox_state import (
    SANDBOX_BACKENDS,
    get_sandbox_id_from_metadata,
    set_sandbox_backend,
    unwrap_sandbox_backend,
)


async def _start_langsmith_sandbox_if_needed(sandbox_backend: SandboxBackendProtocol) -> None:
    """Start a LangSmith sandbox before operations that require it to be running."""
    if os.getenv("SANDBOX_TYPE", "langsmith") != "langsmith":
        return
    current_backend = unwrap_sandbox_backend(sandbox_backend)
    if not isinstance(current_backend, LangSmithSandbox):
        return

    sandbox = current_backend._sandbox  # noqa: SLF001
    status = await asyncio.to_thread(sandbox._client.get_sandbox_status, sandbox.name)  # noqa: SLF001
    status_name = getattr(status, "status", status)
    status_name = getattr(status_name, "value", status_name)
    status_text = str(status_name or "").lower()
    if status_text in {"running", "ready"}:
        return

    logger.info(
        "Starting LangSmith sandbox %s (status=%s)",
        current_backend.id,
        status_text or "unknown",
    )
    await asyncio.to_thread(sandbox.start)


async def _create_sandbox() -> SandboxBackendProtocol:
    """Create a new sandbox."""
    sandbox_backend = await asyncio.to_thread(create_sandbox)
    await _start_langsmith_sandbox_if_needed(sandbox_backend)
    return sandbox_backend


async def _recreate_sandbox(thread_id: str) -> SandboxBackendProtocol:
    """Recreate a sandbox after a connection failure."""
    await client.threads.update(
        thread_id=thread_id,
        metadata={"sandbox_id": SANDBOX_CREATING},
    )
    try:
        sandbox_backend = set_sandbox_backend(thread_id, await _create_sandbox())
    except Exception:
        logger.exception("Failed to recreate sandbox after connection failure")
        await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": None})
        raise
    return sandbox_backend


async def check_or_recreate_sandbox(
    sandbox_backend: SandboxBackendProtocol, thread_id: str
) -> SandboxBackendProtocol:
    """Check if a cached sandbox is reachable; recreate it if not."""
    try:
        await asyncio.to_thread(sandbox_backend.execute, "echo ok")
    except SandboxClientError:
        logger.warning(
            "Cached sandbox is no longer reachable for thread %s, recreating",
            thread_id,
        )
        sandbox_backend = await _recreate_sandbox(thread_id)
    return sandbox_backend


async def _wait_for_sandbox_id(thread_id: str) -> str:
    """Wait for sandbox_id to be set in thread metadata."""
    elapsed = 0.0
    while elapsed < SANDBOX_CREATION_TIMEOUT:
        sandbox_id = await get_sandbox_id_from_metadata(thread_id)
        if sandbox_id is not None and sandbox_id != SANDBOX_CREATING:
            return sandbox_id
        await asyncio.sleep(SANDBOX_POLL_INTERVAL)
        elapsed += SANDBOX_POLL_INTERVAL

    msg = f"Timeout waiting for sandbox creation for thread {thread_id}"
    raise TimeoutError(msg)


def graph_loaded_for_execution(config: RunnableConfig) -> bool:
    """Check if the graph is loaded for actual execution vs introspection."""
    return (
        config["configurable"].get("__is_for_execution__", False)
        if "configurable" in config
        else False
    )


async def ensure_sandbox_for_thread(thread_id: str) -> SandboxBackendProtocol:
    """Get-or-create a healthy sandbox bound to ``thread_id``."""
    sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
    sandbox_id = await get_sandbox_id_from_metadata(thread_id)

    if sandbox_id == SANDBOX_CREATING and not sandbox_backend:
        logger.info("Sandbox creation in progress for thread %s, waiting...", thread_id)
        sandbox_id = await _wait_for_sandbox_id(thread_id)

    if sandbox_backend:
        logger.info("Using cached sandbox backend for thread %s", thread_id)
        sandbox_backend = await check_or_recreate_sandbox(sandbox_backend, thread_id)
    elif sandbox_id is None:
        logger.info("Creating new sandbox for thread %s", thread_id)
        await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": SANDBOX_CREATING})
        try:
            sandbox_backend = await _create_sandbox()
            logger.info("Sandbox created: %s", sandbox_backend.id)
        except Exception:
            logger.exception("Failed to create sandbox")
            try:
                await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": None})
            except Exception:
                logger.exception("Failed to reset sandbox_id metadata")
            raise
    else:
        logger.info("Connecting to existing sandbox %s", sandbox_id)
        created_replacement_sandbox = False
        try:
            sandbox_backend = await asyncio.to_thread(create_sandbox, sandbox_id)
        except Exception:
            logger.warning("Failed to connect to existing sandbox %s, creating new one", sandbox_id)
            await client.threads.update(
                thread_id=thread_id, metadata={"sandbox_id": SANDBOX_CREATING}
            )
            try:
                sandbox_backend = await _create_sandbox()
                created_replacement_sandbox = True
            except Exception:
                logger.exception("Failed to create replacement sandbox")
                await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": None})
                raise
        if not created_replacement_sandbox:
            sandbox_backend = await check_or_recreate_sandbox(sandbox_backend, thread_id)

    sandbox_backend = set_sandbox_backend(thread_id, sandbox_backend)

    if sandbox_id != sandbox_backend.id:
        await client.threads.update(
            thread_id=thread_id, metadata={"sandbox_id": sandbox_backend.id}
        )

    return sandbox_backend


DEFAULT_LLM_MODEL_ID = "openai:gpt-5.5"
DEFAULT_LLM_REASONING: OpenAIReasoning = {"effort": "medium"}
DEFAULT_LLM_MAX_TOKENS = 64_000
DEFAULT_RECURSION_LIMIT = 9_999
MODEL_CALL_RECURSION_LIMIT = 5_000


def _openai_reasoning_for(profile_effort: str | None) -> OpenAIReasoning | None:
    effort = profile_effort or DEFAULT_LLM_REASONING.get("effort")
    if effort == "none":
        return {"effort": "none"}
    if effort == "low":
        return {"effort": "low"}
    if effort == "medium":
        return {"effort": "medium"}
    if effort == "high":
        return {"effort": "high"}
    if effort == "xhigh":
        return {"effort": "xhigh"}
    return None


_ANTHROPIC_EFFORTS: set[AnthropicEffort] = {"low", "medium", "high", "xhigh", "max"}


def _anthropic_thinking_for(profile_effort: str | None) -> AnthropicThinking | None:
    if profile_effort in _ANTHROPIC_EFFORTS:
        return {"type": "adaptive"}
    return None


def _anthropic_effort_for(profile_effort: str | None) -> AnthropicEffort | None:
    if profile_effort in _ANTHROPIC_EFFORTS:
        return profile_effort
    return None


def _get_cached_sandbox_backend(thread_id: str) -> SandboxBackendProtocol:
    sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
    if sandbox_backend is None:
        raise RuntimeError(f"No sandbox backend cached for thread {thread_id}")
    return sandbox_backend


async def get_agent(config: RunnableConfig) -> Pregel:
    """Get or create an agent with a sandbox for the given thread."""
    thread_id = config["configurable"].get("thread_id", None)

    config["recursion_limit"] = DEFAULT_RECURSION_LIMIT

    if thread_id is None or not graph_loaded_for_execution(config):
        logger.info("No thread_id or not for execution, returning agent without sandbox")
        return create_deep_agent(
            system_prompt="",
            tools=[],
        ).with_config(config)

    sandbox_backend = await ensure_sandbox_for_thread(thread_id)

    work_dir = await aresolve_sandbox_work_dir(sandbox_backend)

    def backend_factory(_runtime: object, _thread_id: str = thread_id) -> SandboxBackendProtocol:
        return _get_cached_sandbox_backend(_thread_id)

    model_id = os.environ.get("LLM_MODEL_ID", DEFAULT_LLM_MODEL_ID)
    profile_effort: str | None = None
    profile_login = resolve_profile_login(config)
    if profile_login:
        profile = await load_profile(profile_login)
        if profile:
            overridden_model, overridden_effort = normalize_profile_overrides(profile)
            if overridden_model:
                logger.info(
                    "Applying dashboard profile override for %s: model=%s effort=%s",
                    profile_login,
                    overridden_model,
                    overridden_effort,
                )
                model_id = overridden_model
                profile_effort = overridden_effort

    model_kwargs: ModelKwargs = {"max_tokens": DEFAULT_LLM_MAX_TOKENS}
    if model_id.startswith("openai:"):
        reasoning = _openai_reasoning_for(profile_effort)
        if reasoning is not None:
            model_kwargs["reasoning"] = reasoning
    elif model_id.startswith("anthropic:"):
        thinking = _anthropic_thinking_for(profile_effort)
        if thinking is not None:
            model_kwargs["thinking"] = thinking
        effort = _anthropic_effort_for(profile_effort)
        if effort is not None:
            model_kwargs["effort"] = effort

    fallback_model_id = os.environ.get("LLM_FALLBACK_MODEL_ID") or fallback_model_id_for(model_id)
    fallback_middleware: list[Any] = []
    if fallback_model_id and fallback_model_id != model_id:
        fallback_kwargs: ModelKwargs = {"max_tokens": DEFAULT_LLM_MAX_TOKENS}
        if fallback_model_id.startswith("openai:"):
            fallback_kwargs["reasoning"] = DEFAULT_LLM_REASONING
        fallback_middleware.append(
            ModelFallbackMiddleware(make_model(fallback_model_id, **fallback_kwargs))
        )
        logger.info("Configured model fallback %s -> %s", model_id, fallback_model_id)

    logger.info("Returning agent with sandbox for thread %s", thread_id)
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
