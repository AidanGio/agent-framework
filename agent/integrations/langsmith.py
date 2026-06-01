"""LangSmith sandbox backend integration."""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import Any

from deepagents.backends import LangSmithSandbox
from deepagents.backends.protocol import SandboxBackendProtocol
from langsmith.sandbox import SandboxClient

logger = logging.getLogger(__name__)

DEFAULT_SNAPSHOT_FS_CAPACITY_BYTES = 32 * 1024**3
DEFAULT_SANDBOX_VCPUS = 2
DEFAULT_SANDBOX_MEM_BYTES = 7936 * 1024**2  # 7936 MiB ("large" tier cap)
DEFAULT_SANDBOX_IDLE_TTL_SECONDS = 10 * 60  # 10 minutes
DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS = 24 * 60 * 60  # 24 hours


def _get_langsmith_api_key() -> str | None:
    """Get LangSmith API key from environment.

    Checks LANGSMITH_API_KEY first, then falls back to LANGSMITH_API_KEY_PROD
    for LangGraph Cloud deployments where LANGSMITH_API_KEY is reserved.
    """
    return os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGSMITH_API_KEY_PROD")


def _parse_optional_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as e:
        msg = f"{name} must be an integer, got {raw!r}"
        raise ValueError(msg) from e


def _get_sandbox_snapshot_config() -> tuple[str | None, int, int, int, int, int]:
    """Get sandbox snapshot configuration from environment."""
    snapshot_id = os.environ.get("DEFAULT_SANDBOX_SNAPSHOT_ID")
    fs_capacity_bytes = _parse_optional_int(
        "DEFAULT_SANDBOX_SNAPSHOT_FS_CAPACITY_BYTES", DEFAULT_SNAPSHOT_FS_CAPACITY_BYTES
    )
    vcpus = _parse_optional_int("DEFAULT_SANDBOX_VCPUS", DEFAULT_SANDBOX_VCPUS)
    mem_bytes = _parse_optional_int("DEFAULT_SANDBOX_MEM_BYTES", DEFAULT_SANDBOX_MEM_BYTES)
    idle_ttl_seconds = _parse_optional_int(
        "DEFAULT_SANDBOX_IDLE_TTL_SECONDS", DEFAULT_SANDBOX_IDLE_TTL_SECONDS
    )
    delete_after_stop_seconds = _parse_optional_int(
        "DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS",
        DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS,
    )
    return (
        snapshot_id,
        fs_capacity_bytes,
        vcpus,
        mem_bytes,
        idle_ttl_seconds,
        delete_after_stop_seconds,
    )


def create_langsmith_sandbox(
    sandbox_id: str | None = None,
) -> SandboxBackendProtocol:
    """Create or connect to a LangSmith sandbox without automatic cleanup."""
    api_key = _get_langsmith_api_key()
    (
        snapshot_id,
        fs_capacity_bytes,
        vcpus,
        mem_bytes,
        idle_ttl_seconds,
        delete_after_stop_seconds,
    ) = _get_sandbox_snapshot_config()

    provider = LangSmithProvider(api_key=api_key)
    backend = provider.get_or_create(
        sandbox_id=sandbox_id,
        snapshot_id=snapshot_id,
        fs_capacity_bytes=fs_capacity_bytes,
        vcpus=vcpus,
        mem_bytes=mem_bytes,
        idle_ttl_seconds=idle_ttl_seconds,
        delete_after_stop_seconds=delete_after_stop_seconds,
    )
    _update_thread_sandbox_metadata(backend.id)
    return backend


def _update_thread_sandbox_metadata(sandbox_id: str) -> None:
    """Update thread metadata with sandbox_id."""
    try:
        import asyncio

        from langgraph.config import get_config
        from langgraph_sdk import get_client

        config = get_config()
        thread_id = config.get("configurable", {}).get("thread_id")
        if not thread_id:
            return
        client = get_client()

        async def _update() -> None:
            await client.threads.update(
                thread_id=thread_id,
                metadata={"sandbox_id": sandbox_id},
            )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_update())
        else:
            loop.create_task(_update())
    except Exception:
        pass


class SandboxProvider(ABC):
    """Interface for creating and deleting sandbox backends."""

    @abstractmethod
    def get_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        **kwargs: Any,
    ) -> SandboxBackendProtocol:
        """Get an existing sandbox, or create one if needed."""
        raise NotImplementedError

    @abstractmethod
    def delete(
        self,
        *,
        sandbox_id: str,
        **kwargs: Any,
    ) -> None:
        """Delete a sandbox by id."""
        raise NotImplementedError


class LangSmithProvider(SandboxProvider):
    """LangSmith sandbox provider implementation."""

    def __init__(self, api_key: str | None = None) -> None:
        from langsmith import sandbox

        self._api_key = api_key or _get_langsmith_api_key()
        if not self._api_key:
            msg = "LANGSMITH_API_KEY (or LANGSMITH_API_KEY_PROD) not set"
            raise ValueError(msg)
        self._client: SandboxClient = sandbox.SandboxClient(api_key=self._api_key)

    @classmethod
    def validate_startup_config(cls) -> None:
        """Validate env-var configuration at server startup. Raises ValueError if invalid."""
        if not os.environ.get("DEFAULT_SANDBOX_SNAPSHOT_ID"):
            msg = "DEFAULT_SANDBOX_SNAPSHOT_ID must be set when SANDBOX_TYPE=langsmith"
            raise ValueError(msg)
        for name in (
            "DEFAULT_SANDBOX_SNAPSHOT_FS_CAPACITY_BYTES",
            "DEFAULT_SANDBOX_VCPUS",
            "DEFAULT_SANDBOX_MEM_BYTES",
            "DEFAULT_SANDBOX_IDLE_TTL_SECONDS",
            "DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS",
        ):
            raw = os.environ.get(name)
            if raw is None or raw == "":
                continue
            try:
                value = int(raw)
            except ValueError as e:
                msg = f"{name} must be an integer, got {raw!r}"
                raise ValueError(msg) from e
            if (
                name
                in {
                    "DEFAULT_SANDBOX_IDLE_TTL_SECONDS",
                    "DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS",
                }
                and value < 0
            ):
                msg = f"{name} must be >= 0, got {value}"
                raise ValueError(msg)

    def get_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        timeout: int = 180,
        snapshot_id: str | None = None,
        fs_capacity_bytes: int | None = None,
        vcpus: int | None = None,
        mem_bytes: int | None = None,
        idle_ttl_seconds: int | None = None,
        delete_after_stop_seconds: int | None = None,
        **kwargs: Any,
    ) -> SandboxBackendProtocol:
        """Get existing or create new LangSmith sandbox."""
        if kwargs:
            msg = f"Received unsupported arguments: {list(kwargs.keys())}"
            raise TypeError(msg)
        if sandbox_id:
            try:
                sandbox = self._client.get_sandbox(name=sandbox_id)
            except Exception as e:
                msg = f"Failed to connect to existing sandbox '{sandbox_id}': {e}"
                raise RuntimeError(msg) from e
            return LangSmithSandbox(sandbox)

        if not snapshot_id:
            msg = "DEFAULT_SANDBOX_SNAPSHOT_ID must be set when SANDBOX_TYPE=langsmith"
            raise ValueError(msg)

        try:
            sandbox = self._client.create_sandbox(
                snapshot_id=snapshot_id,
                fs_capacity_bytes=fs_capacity_bytes,
                vcpus=vcpus,
                mem_bytes=mem_bytes,
                idle_ttl_seconds=idle_ttl_seconds,
                delete_after_stop_seconds=delete_after_stop_seconds,
                timeout=timeout,
            )
        except Exception as e:
            msg = f"Failed to create sandbox from snapshot '{snapshot_id}': {e}"
            raise RuntimeError(msg) from e

        return LangSmithSandbox(sandbox)

    def delete(self, *, sandbox_id: str, **kwargs: Any) -> None:
        """Delete a LangSmith sandbox."""
        self._client.delete_sandbox(sandbox_id)
