"""Tests for the public MCP server surface.

Tool logic is already covered by test_tool_executor / test_sefaria_client /
test_catalog_service, so these tests cover only what the MCP surface adds:
which tools are exposed, that they round-trip through FastMCP, and the setup
transport contract at /mcp.
"""

import json
import os
import re
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastmcp import Client

from chat.V2.agent import tool_schemas
from chat.V2.agent.tool_executor import SefariaToolExecutor
from mcp_server import app as app_module
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
        assert tools[schema["name"]].input_schema == schema["input_schema"]


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


# Runs in a subprocess because the test suite always configures Django, which
# hides tool-layer code that reads settings. The MCP process never configures it.
_STANDALONE_SCRIPT = r"""
import asyncio, functools, json, os, sys
import httpx

seen = []

def handler(request):
    seen.append(request.headers["User-Agent"])
    return httpx.Response(200, json={"versions": [{"text": "In the beginning"}]})

httpx.AsyncClient = functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(handler))

from fastmcp import Client
from mcp_server.app import build_server

async def main():
    async with Client(build_server()) as client:
        result = await client.call_tool("get_text", {"reference": "Genesis 1:1"})
    heavy = [m for m in ("claude_agent_sdk", "anthropic", "braintrust") if m in sys.modules]
    print(json.dumps({"text": result.content[0].text, "ua": seen, "heavy": heavy}))

asyncio.run(main())
"""


def test_tools_run_without_django_settings():
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    env.update(ENVIRONMENT="prod", SEFARIA_USER_AGENT_SERVICE="mcp")
    proc = subprocess.run(
        [sys.executable, "-c", _STANDALONE_SCRIPT],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert "In the beginning" in out["text"]
    assert out["ua"] and set(out["ua"]) == {"Sefaria/mcp (prod)"}
    assert out["heavy"] == []


# ---------------------------------------------------------------------------
# Transport: stateless Streamable HTTP at /mcp, guarded by Host/Origin
# ---------------------------------------------------------------------------

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}
MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


@asynccontextmanager
async def http_client(executor):
    """An HTTP client bound to the ASGI app, with its lifespan running.

    The Streamable HTTP transport builds its session manager in the lifespan, so it
    500s without one. Used inline rather than as a fixture so the lifespan's task
    group is entered and exited in the same task.
    """
    app = build_app(executor)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://mcp.test") as client:
            yield client


async def _initialize(executor, **headers):
    async with http_client(executor) as client:
        return await client.post("/mcp", json=INIT, headers={**MCP_HEADERS, **headers})


@pytest.mark.parametrize("host", ["chat.sefaria.org", "chat-dev.sefaria.org", "localhost"])
async def test_mcp_initializes_for_allowed_hosts(executor, host):
    r = await _initialize(executor, Host=host)
    assert r.status_code == 200
    assert '"protocolVersion"' in r.text


async def test_mcp_accepts_allowlisted_origin(executor):
    r = await _initialize(executor, Host="chat.sefaria.org", Origin="https://chat.sefaria.org")
    assert r.status_code == 200


async def test_mcp_rejects_unlisted_host(executor):
    assert (await _initialize(executor, Host="evil.example.com")).status_code == 421


async def test_mcp_rejects_unlisted_origin(executor):
    r = await _initialize(executor, Host="chat.sefaria.org", Origin="https://evil.example.com")
    assert r.status_code == 403


async def test_allowlists_come_from_env(executor, monkeypatch):
    monkeypatch.setenv("SEFARIA_MCP_ALLOWED_HOSTS", "other.test")
    assert (await _initialize(executor, Host="other.test")).status_code == 200
    assert (await _initialize(executor, Host="chat.sefaria.org")).status_code == 421


async def test_host_protection_escape_hatch(executor, monkeypatch):
    monkeypatch.setenv("SEFARIA_MCP_HOST_PROTECTION", "off")
    assert (await _initialize(executor, Host="evil.example.com")).status_code == 200


async def test_mcp_is_stateless(executor):
    """No session id is issued, so any replica can serve any request."""
    r = await _initialize(executor, Host="chat.sefaria.org")
    assert "mcp-session-id" not in r.headers


@pytest.mark.parametrize("method", ["GET", "PUT", "PATCH"])
async def test_mcp_unsupported_methods_are_405_not_404(executor, method):
    """A 404 would tell a probing client there is no endpoint here at all."""
    async with http_client(executor) as client:
        r = await client.request(method, "/mcp", headers={"Host": "chat.sefaria.org"})
    assert r.status_code == 405


async def test_no_sse_transport(executor):
    """SSE clients stay on the old server; this endpoint is Streamable HTTP only."""
    async with http_client(executor) as client:
        assert (await client.get("/sse")).status_code == 404
        assert (await client.post("/messages/", json={})).status_code == 404


async def test_mcp_does_not_redirect_trailing_slash(executor):
    async with http_client(executor) as client:
        r = await client.post("/mcp/", json=INIT, headers={**MCP_HEADERS, "Host": "chat.sefaria.org"})
    assert r.status_code == 404


async def test_healthz_is_unguarded(executor):
    """Kubelet probes use the pod IP as Host, which the /mcp guard would reject."""
    async with http_client(executor) as client:
        r = await client.get("/mcp/healthz", headers={"Host": "10.0.0.7:8088"})
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# OAuth discovery (SEFARIA_MCP_DISCOVERY)
# ---------------------------------------------------------------------------

PROTECTED_RESOURCE = "/.well-known/oauth-protected-resource/mcp"


async def test_discovery_defaults_to_empty_stub(executor, monkeypatch):
    """The stub is what claude.ai's Connect needed on the old server (PRs #25 -> #28)."""
    monkeypatch.delenv("SEFARIA_MCP_DISCOVERY", raising=False)
    monkeypatch.setenv("SEFARIA_MCP_PUBLIC_URL", "https://chat-dev.sefaria.org/mcp/")
    async with http_client(executor) as client:
        r = await client.get(PROTECTED_RESOURCE, headers={"Host": "evil.example.com"})
    assert r.status_code == 200
    assert r.json() == {"resource": "https://chat-dev.sefaria.org/mcp", "authorization_servers": []}


async def test_discovery_none_serves_nothing(executor, monkeypatch):
    """RFC 9728: absence of the document means "no auth required"."""
    monkeypatch.setenv("SEFARIA_MCP_DISCOVERY", "none")
    async with http_client(executor) as client:
        assert (await client.get(PROTECTED_RESOURCE)).status_code == 404


async def test_invalid_discovery_mode_falls_back_to_stub(executor, monkeypatch):
    monkeypatch.setenv("SEFARIA_MCP_DISCOVERY", "bogus")
    async with http_client(executor) as client:
        assert (await client.get(PROTECTED_RESOURCE)).status_code == 200


async def test_host_root_discovery_is_not_ours(executor):
    """On the chat host the root /.well-known/* paths belong to Django."""
    async with http_client(executor) as client:
        for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-authorization-server"):
            assert (await client.get(path)).status_code == 404


# ---------------------------------------------------------------------------
# Environment parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, "", " , , "])
def test_env_list_falls_back_when_empty(monkeypatch, raw):
    """A blank ConfigMap value must not become an empty, authoritative allowlist."""
    if raw is None:
        monkeypatch.delenv("X_LIST", raising=False)
    else:
        monkeypatch.setenv("X_LIST", raw)
    assert app_module._env_list("X_LIST", ["a"]) == ["a"]


def test_env_list_parses_and_strips(monkeypatch):
    monkeypatch.setenv("X_LIST", " a.test ,b.test,, ")
    assert app_module._env_list("X_LIST", ["zzz"]) == ["a.test", "b.test"]


@pytest.mark.parametrize(("raw", "expected"), [("notanumber", 8088), ("8899", 8899)])
def test_env_int(monkeypatch, raw, expected):
    monkeypatch.setenv("X_INT", raw)
    assert app_module._env_int("X_INT", 8088) == expected
