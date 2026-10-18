import asyncio
import hashlib
import json
import logging
import os
from typing import Any

from exa_py import Exa
from httpx import AsyncClient
from langgraph.config import get_config
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

logger = logging.getLogger(__name__)


def _error_detail(error: Exception) -> str:
    if isinstance(error, ExceptionGroup):
        return "; ".join(_error_detail(child) for child in error.exceptions)
    return f"{type(error).__name__}: {error}"


PARALLEL_MCP_URL = "https://search.parallel.ai/mcp"
SEARCH_USER_AGENT = "agent-framework/0.1.0 (+https://github.com/AidanGio/agent-framework)"
SEARCH_TIMEOUT = 30.0


def _conversation_id() -> str | None:
    """Reuse the graph thread identity without sending the original identifier."""
    try:
        thread_id = get_config().get("configurable", {}).get("thread_id")
    except RuntimeError:
        return None
    return hashlib.sha256(str(thread_id).encode()).hexdigest() if thread_id else None


async def _parallel_search(query: str, num_results: int, include_contents: bool) -> str:
    arguments: dict[str, Any] = {"objective": query, "search_queries": [query]}
    session_id = _conversation_id()
    if session_id:
        arguments["session_id"] = session_id

    # Bound the entire handshake/discovery/call, as well as individual HTTP reads.
    async with asyncio.timeout(SEARCH_TIMEOUT):
        async with AsyncClient(
            headers={"User-Agent": SEARCH_USER_AGENT}, timeout=SEARCH_TIMEOUT
        ) as http_client:
            async with streamable_http_client(PARALLEL_MCP_URL, http_client=http_client) as (
                read,
                write,
                _,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    if not any(tool.name == "web_search" for tool in tools.tools):
                        raise ValueError("Parallel MCP does not expose web_search")
                    response = await session.call_tool("web_search", arguments)
                    if response.isError:
                        detail = "\n".join(
                            block.text for block in response.content if block.type == "text"
                        )
                        raise ValueError(detail or "Parallel MCP search failed")
                    payload = response.structuredContent
                    if payload is None:
                        text = next(
                            (block.text for block in response.content if block.type == "text"), ""
                        )
                        payload = json.loads(text)
                    if not isinstance(payload, dict) or not isinstance(
                        payload.get("results"), list
                    ):
                        raise ValueError("Parallel MCP returned an invalid search response")
                    # The anonymous server controls retrieval limits. Apply this tool's
                    # existing count/content options to the returned results locally.
                    results = []
                    for result in payload["results"][:num_results]:
                        item = dict(result)
                        if not include_contents:
                            item.pop("excerpts", None)
                            item.pop("full_content", None)
                        results.append(item)
                    return json.dumps(results, ensure_ascii=False)


async def aweb_search(
    query: str,
    num_results: int = 5,
    include_contents: bool = True,
) -> dict[str, Any]:
    """Async execution path used by the agent's tool dispatcher."""
    provider = os.environ.get("WEB_SEARCH_PROVIDER", "").strip().lower()
    api_key = os.environ.get("EXA_API_KEY")
    provider = provider or ("exa" if api_key else "parallel")
    if provider == "none":
        return {"success": False, "results": None, "error": "Web search is disabled."}
    if provider not in {"exa", "parallel"}:
        return {
            "success": False,
            "results": None,
            "error": "WEB_SEARCH_PROVIDER must be parallel, exa, or none.",
        }
    if provider == "exa" and not api_key:
        return {
            "success": False,
            "results": None,
            "error": "EXA_API_KEY is not configured. Please add it to your environment variables.",
        }
    if not query.strip() or not 1 <= num_results <= 100:
        return {
            "success": False,
            "results": None,
            "error": "Provide a nonempty query and num_results between 1 and 100.",
        }

    try:
        if provider == "parallel":
            results = await _parallel_search(query, num_results, include_contents)
        else:
            client = Exa(api_key=api_key)
            if include_contents:
                result = await asyncio.to_thread(
                    client.search_and_contents,
                    query,
                    text=True,
                    num_results=num_results,
                    type="auto",
                )
            else:
                result = await asyncio.to_thread(
                    client.search, query, num_results=num_results, type="auto"
                )
            results = str(result)
        return {"success": True, "results": results, "error": None}
    except Exception as e:
        logger.exception("web_search failed")
        return {"success": False, "results": None, "error": _error_detail(e)}


def web_search(
    query: str,
    num_results: int = 5,
    include_contents: bool = True,
) -> dict[str, Any]:
    """Search the web for documentation, articles, and current information.

    Uses Parallel Search MCP without an API key unless Exa is configured.
    Search results include source URLs; use fetch_url when more page content is needed.

    Args:
        query: The search query.
        num_results: Maximum number of results to return (1–100, default: 5).
            Parallel may return fewer results, according to its server-managed limits.
        include_contents: Include relevant excerpts with Parallel, or full page text
            with Exa (default: True).

    Returns:
        Dictionary with success, results (a string), and error fields.
    """
    return asyncio.run(aweb_search(query, num_results, include_contents))
