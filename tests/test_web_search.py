"""Exercise provider selection and MCP over the real HTTP client and tool dispatcher."""

import asyncio
import hashlib
import importlib
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

search = importlib.import_module("agent.tools.web_search")
REAL_ASYNC_CLIENT = httpx.AsyncClient
SOURCE_URL = "https://docs.python.org/3/library/asyncio.html"


@pytest.fixture(autouse=True)
def isolated_search_config(monkeypatch):
    for key in ("EXA_API_KEY", "PARALLEL_API_KEY", "WEB_SEARCH_PROVIDER"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def mcp_server(monkeypatch):
    requests = []
    state = {"failure": None, "text_only": False, "calls_started": asyncio.Event()}

    async def handle(request):
        requests.append(request)
        assert str(request.url) == search.PARALLEL_MCP_URL
        assert request.headers["user-agent"] == search.SEARCH_USER_AGENT
        assert "authorization" not in request.headers
        message = json.loads(request.content)
        method = message["method"]
        if "id" not in message:
            return httpx.Response(202)
        if method == "initialize":
            result = {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test-parallel", "version": "1"},
            }
        elif method == "tools/list":
            result = {"tools": [{"name": "web_search", "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            state["calls_started"].set()
            failure = state["failure"]
            if failure == "wait":
                await asyncio.Event().wait()
            if failure == "http":
                return httpx.Response(503, text="Service unavailable")
            if failure == "rpc":
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {"code": -32602, "message": "Invalid search request"},
                    },
                )
            if failure == "malformed":
                return httpx.Response(
                    200, text="not JSON", headers={"content-type": "application/json"}
                )
            payload = {
                "results": [
                    {"url": SOURCE_URL, "title": "asyncio", "excerpts": ["Python async I/O"]},
                    {"url": "https://example.com/second", "title": "Second", "excerpts": ["Extra"]},
                ]
            }
            if failure == "shape":
                payload = {"unexpected": []}
            result = {"content": [{"type": "text", "text": json.dumps(payload)}]}
            if not state["text_only"]:
                result["structuredContent"] = payload
            if failure == "tool":
                result = {"isError": True, "content": [{"type": "text", "text": "Quota reached"}]}
        else:
            raise AssertionError(method)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})

    def client(**kwargs):
        return REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(search, "AsyncClient", client)
    return requests, state


async def test_fresh_install_search_and_local_options(mcp_server):
    requests, state = mcp_server
    result = await search.aweb_search("Python asyncio documentation", num_results=1)
    assert result["success"] is True and result["error"] is None
    rows = json.loads(result["results"])
    assert rows == [{"url": SOURCE_URL, "title": "asyncio", "excerpts": ["Python async I/O"]}]
    methods = [json.loads(r.content)["method"] for r in requests]
    assert methods == ["initialize", "notifications/initialized", "tools/list", "tools/call"]
    call = json.loads(requests[-1].content)
    assert call["params"]["name"] == "web_search"
    assert call["params"]["arguments"] == {
        "objective": "Python asyncio documentation",
        "search_queries": ["Python asyncio documentation"],
    }
    state["text_only"] = True
    result = await search.aweb_search("Python asyncio documentation", include_contents=False)
    assert len(json.loads(result["results"])) == 2
    assert all("excerpts" not in row for row in json.loads(result["results"]))


def test_sync_api_uses_same_resolver(mcp_server):
    assert search.web_search("Python asyncio documentation")["success"] is True


@pytest.mark.parametrize("provider", [None, "exa"])
@pytest.mark.parametrize("contents", [True, False])
async def test_configured_exa_is_preserved(monkeypatch, mcp_server, provider, contents):
    requests, _ = mcp_server
    monkeypatch.setenv("EXA_API_KEY", "configured-exa-key")
    if provider:
        monkeypatch.setenv("WEB_SEARCH_PROVIDER", provider)
    calls = []

    class Exa:
        def __init__(self, *, api_key):
            assert api_key == "configured-exa-key"

        def search(self, query, **kwargs):
            calls.append(("search", query, kwargs))
            return "Exa results"

        def search_and_contents(self, query, **kwargs):
            calls.append(("contents", query, kwargs))
            return "Exa contents"

    monkeypatch.setattr(search, "Exa", Exa)
    result = await search.aweb_search("query", 3, contents)
    assert result == {
        "success": True,
        "results": "Exa contents" if contents else "Exa results",
        "error": None,
    }
    assert calls == [
        (
            "contents" if contents else "search",
            "query",
            {"num_results": 3, "type": "auto", **({"text": True} if contents else {})},
        )
    ]
    assert requests == []


async def test_explicit_parallel_overrides_exa_key(monkeypatch, mcp_server):
    monkeypatch.setenv("EXA_API_KEY", "configured-exa-key")
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "parallel")
    monkeypatch.setattr(search, "Exa", lambda **kwargs: pytest.fail("Exa must not be used"))
    assert (await search.aweb_search("query"))["success"] is True


@pytest.mark.parametrize(
    "provider,detail",
    [("none", "disabled"), ("exa", "EXA_API_KEY"), ("unknown", "WEB_SEARCH_PROVIDER")],
)
async def test_disabled_missing_key_and_invalid_provider_do_not_send(
    monkeypatch, mcp_server, provider, detail
):
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", provider)
    result = await search.aweb_search("query")
    assert result["success"] is False and detail in result["error"]
    assert mcp_server[0] == []


async def test_exa_failure_does_not_fallback(monkeypatch, mcp_server):
    monkeypatch.setenv("EXA_API_KEY", "configured-exa-key")

    def fail(**kwargs):
        raise ValueError("Exa unavailable")

    monkeypatch.setattr(search, "Exa", fail)
    result = await search.aweb_search("query")
    assert result["success"] is False and "Exa unavailable" in result["error"]
    assert mcp_server[0] == []


@pytest.mark.parametrize("failure", ["http", "rpc", "tool", "malformed", "shape"])
async def test_mcp_failures_are_errors_without_fallback(monkeypatch, mcp_server, failure):
    mcp_server[1]["failure"] = failure
    monkeypatch.setattr(search, "SEARCH_TIMEOUT", 0.5)
    monkeypatch.setattr(search, "Exa", lambda **kwargs: pytest.fail("Must not fall back"))
    result = await search.aweb_search("query")
    assert result["success"] is False and result["results"] is None and result["error"]


async def test_mcp_timeout_closes_request(monkeypatch, mcp_server):
    mcp_server[1]["failure"] = "wait"
    monkeypatch.setattr(search, "SEARCH_TIMEOUT", 0.05)
    result = await search.aweb_search("query")
    assert result["success"] is False and "TimeoutError" in result["error"]


async def test_mcp_cancellation_propagates(mcp_server):
    mcp_server[1]["failure"] = "wait"
    task = asyncio.create_task(search.aweb_search("query"))
    await asyncio.wait_for(mcp_server[1]["calls_started"].wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("query,count", [(" ", 5), ("query", 0), ("query", 101)])
async def test_bad_arguments_do_not_send(mcp_server, query, count):
    assert (await search.aweb_search(query, count))["success"] is False
    assert mcp_server[0] == []


class _SearchModel(BaseChatModel):
    """Scripted model: verify the real agent consumes the returned tool message."""

    expected_success: bool = True

    @property
    def _llm_type(self):
        return "scripted-search-test"

    def bind_tools(self, tools, **kwargs):
        assert any(getattr(tool, "name", None) == "web_search" for tool in tools)
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        responses = [m for m in messages if isinstance(m, ToolMessage) and m.name == "web_search"]
        if not responses:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "web_search",
                        "args": {"query": "Python asyncio documentation"},
                        "id": "search-1",
                    }
                ],
            )
        else:
            payload = json.loads(responses[-1].content)
            assert payload["success"] is self.expected_success
            if self.expected_success:
                row = json.loads(payload["results"])[0]
                assert row["url"] == SOURCE_URL
                message = AIMessage(content=f"Found {row['title']}: {row['url']}")
            else:
                assert payload["error"] and payload["results"] is None
                message = AIMessage(content=f"Search failed: {payload['error']}")
        return ChatResult(generations=[ChatGeneration(message=message)])


async def _registered_graph(monkeypatch, tmp_path, *, expected_success=True):
    from deepagents.backends import FilesystemBackend

    from agent import server

    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    monkeypatch.setattr(server, "ensure_sandbox_for_thread", AsyncMock(return_value=backend))
    monkeypatch.setattr(server, "aresolve_sandbox_work_dir", AsyncMock(return_value="/"))
    monkeypatch.setattr(server, "_get_cached_sandbox_backend", lambda thread_id: backend)
    monkeypatch.setattr(
        server,
        "make_model",
        lambda *args, **kwargs: _SearchModel(expected_success=expected_success),
    )
    monkeypatch.setenv("LLM_FALLBACK_MODEL_ID", "openai:gpt-5.5")
    config = {"configurable": {"thread_id": "search-test-thread", "__is_for_execution__": True}}
    return await server.get_agent(config), config


@pytest.mark.parametrize("mode", ["normal", "disabled", "http", "tool", "timeout"])
async def test_registered_agent_dispatches_and_consumes_search(
    monkeypatch, tmp_path, mcp_server, mode
):
    if mode == "disabled":
        monkeypatch.setenv("WEB_SEARCH_PROVIDER", "none")
    elif mode in {"http", "tool"}:
        mcp_server[1]["failure"] = mode
    elif mode == "timeout":
        mcp_server[1]["failure"] = "wait"
        monkeypatch.setattr(search, "SEARCH_TIMEOUT", 0.1)
    graph, config = await _registered_graph(
        monkeypatch, tmp_path, expected_success=mode == "normal"
    )
    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "Find asyncio docs"}]}, config
    )
    if mode == "normal":
        assert result["messages"][-1].content == f"Found asyncio: {SOURCE_URL}"
    else:
        assert result["messages"][-1].content.startswith("Search failed:")
    if mode == "disabled":
        assert mcp_server[0] == []
        return
    calls = [
        json.loads(r.content)
        for r in mcp_server[0]
        if json.loads(r.content)["method"] == "tools/call"
    ]
    assert (
        calls[0]["params"]["arguments"]["session_id"]
        == hashlib.sha256(b"search-test-thread").hexdigest()
    )


async def test_registered_agent_cancellation_reaches_mcp(monkeypatch, tmp_path, mcp_server):
    mcp_server[1]["failure"] = "wait"
    graph, config = await _registered_graph(monkeypatch, tmp_path)
    task = asyncio.create_task(
        graph.ainvoke({"messages": [{"role": "user", "content": "Search"}]}, config)
    )
    await asyncio.wait_for(mcp_server[1]["calls_started"].wait(), timeout=3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
