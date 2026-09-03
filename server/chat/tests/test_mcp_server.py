"""Tests for the public MCP server surface.

Tool logic is already covered by test_tool_executor / test_sefaria_client /
test_catalog_service, so these tests cover only what the MCP surface adds:
which tools are exposed, that they round-trip through FastMCP, and the setup
contract existing connectors depend on.
"""

import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastmcp import Client

from chat.V2.agent import tool_schemas
from chat.V2.agent.tool_executor import SefariaToolExecutor
from mcp_server.app import build_app, build_server

EXECUTOR_SOURCE = Path(SefariaToolExecutor.__module__.replace(".", "/") + ".py")


def _dispatched_tool_names() -> set[str]:
    """Tool names with a branch in SefariaToolExecutor._dispatch."""
    source = (Path(__file__).resolve().parents[2] / EXECUTOR_SOURCE).read_text()
    return set(re.findall(r'tool_name == "([a-z_]+)"', source))


def _mcp_names() -> set[str]:
    return {t["name"] for t in tool_schemas.get_tools_for_surface("mcp")}


# ---------------------------------------------------------------------------
# Marker invariants
# ---------------------------------------------------------------------------


def test_every_mcp_tool_is_dispatchable():
    """A tool marked for the MCP surface must be executable."""
    missing = _mcp_names() - _dispatched_tool_names()
    assert not missing, f"MCP tools with no _dispatch branch: {sorted(missing)}"


def test_mcp_surface_excludes_tools_needing_user_auth():
    """The server is authless, so nothing requiring a user token may be exposed."""
    assert not (_mcp_names() & tool_schemas.LABS_TOOL_NAMES)


def test_unmarked_tools_default_to_agent_only():
    """Forgetting the marker must not leak a tool onto the public server."""
    assert tool_schemas.has_surface({"name": "x"}, "agent")
    assert not tool_schemas.has_surface({"name": "x"}, "mcp")


def test_agent_surface_is_unchanged_by_mcp_only_tools():
    """MCP-only tools must not reach the agent."""
    agent_names = {t["name"] for t in tool_schemas.get_tools_for_labs(labs=True)}
    assert "get_text_or_category_shape" not in agent_names
    assert "get_text_catalogue_info" not in agent_names
    assert len(tool_schemas.get_all_tools()) == 18


# ---------------------------------------------------------------------------
# Server surface
# ---------------------------------------------------------------------------


@pytest.fixture
def executor():
    client = MagicMock()
    client.get_text = AsyncMock(return_value={"versions": [{"text": "In the beginning"}]})
    client.get_text_or_category_shape = AsyncMock(return_value=[{"title": "Genesis"}])
    client.get_text_catalogue_info = AsyncMock(return_value={"title": "Genesis"})
    return SefariaToolExecutor(client=client)


async def test_list_tools_matches_marked_set(executor):
    async with Client(build_server(executor)) as client:
        assert {t.name for t in await client.list_tools()} == _mcp_names()


async def test_schemas_are_passed_through_verbatim(executor):
    async with Client(build_server(executor)) as client:
        tools = {t.name: t for t in await client.list_tools()}
    for schema in tool_schemas.get_tools_for_surface("mcp"):
        assert tools[schema["name"]].inputSchema == schema["input_schema"]


async def test_tool_call_round_trips(executor):
    async with Client(build_server(executor)) as client:
        result = await client.call_tool("get_text", {"reference": "Genesis 1:1"})
    assert "In the beginning" in result.content[0].text


async def test_mcp_only_tools_are_callable(executor):
    """The two legacy catalogue tools are served from the shared client."""
    async with Client(build_server(executor)) as client:
        shape = await client.call_tool("get_text_or_category_shape", {"name": "Genesis"})
        info = await client.call_tool("get_text_catalogue_info", {"title": "Genesis"})
    assert json.loads(shape.content[0].text) == [{"title": "Genesis"}]
    assert json.loads(info.content[0].text) == {"title": "Genesis"}


async def test_tool_errors_surface_as_mcp_errors(executor):
    executor.client.get_text = AsyncMock(side_effect=RuntimeError("upstream is down"))
    async with Client(build_server(executor)) as client:
        with pytest.raises(Exception, match="upstream is down"):
            await client.call_tool("get_text", {"reference": "Genesis 1:1"})


# ---------------------------------------------------------------------------
# Setup contract for existing connectors
# ---------------------------------------------------------------------------


@asynccontextmanager
async def mcp_http_client(executor):
    """An HTTP client bound to the ASGI app, with its lifespan running.

    The Streamable HTTP transport initializes its session manager in the
    lifespan, so it 500s without one. Used inline rather than as a fixture so the
    lifespan's task group is entered and exited in the same task.
    """
    app = build_app(executor)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as client:
            yield client


@pytest.mark.parametrize(
    "path",
    [
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-protected-resource/sse",
    ],
)
async def test_oauth_discovery_paths_are_absent(executor, path):
    """RFC 9728: absence means "no auth required".

    Serving empty stubs here is what convinced claude.ai the server was
    OAuth-protected and broke connector setup.
    """
    async with mcp_http_client(executor) as client:
        assert (await client.get(path)).status_code == 404


def test_sse_routes_are_served_without_redirects(executor):
    """Existing connectors use /sse exactly.

    Asserted at the routing table rather than by opening the stream, which never
    closes on its own.
    """
    app = build_app(executor)
    paths = {getattr(route, "path", None) for route in app.router.routes}
    assert "/sse" in paths
    assert "/messages" in paths  # SSE client-to-server posts
    assert app.router.redirect_slashes is False


async def test_streamable_http_endpoint_is_served(executor):
    """406 (not 404) proves the route exists and is negotiating content types."""
    async with mcp_http_client(executor) as client:
        assert (await client.get("/mcp")).status_code == 406
