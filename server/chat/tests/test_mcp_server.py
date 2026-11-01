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
from fastmcp.exceptions import ToolError
from prometheus_client import REGISTRY

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


PUBLIC_TOOLS = {
    "get_text",
    "specific_keyword_search",
    "semantic_search",
    "get_current_calendar",
    "get_links_between_texts",
    "search_in_book",
    "search_in_dictionaries",
    "get_english_translations",
    "get_topic_details",
    "clarify_name_argument",
    "clarify_search_path_filter",
    "catalog_get_node",
    "catalog_get_children",
    "catalog_search",
    "catalog_query",
    "get_available_manuscripts",
    "get_manuscript_image",
    "validate_refs",
    "validate_response_links",
}


def test_public_surface_is_exactly_the_reviewed_set():
    """Adding or removing a public tool should be a deliberate, reviewed change."""
    assert _mcp_names() == PUBLIC_TOOLS


def test_agent_surface_is_unchanged_by_mcp_only_tools():
    """MCP-only tools must not reach the agent."""
    agent_names = {t["name"] for t in tool_schemas.get_tools_for_labs(labs=True)}
    assert "validate_response_links" not in agent_names
    assert len(tool_schemas.get_all_tools()) == 18


# ---------------------------------------------------------------------------
# Server surface
# ---------------------------------------------------------------------------


@pytest.fixture
def executor():
    client = MagicMock()
    client.get_text = AsyncMock(return_value={"versions": [{"text": "In the beginning"}]})
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


async def test_tool_errors_surface_as_mcp_errors(executor):
    executor.client.get_text = AsyncMock(side_effect=RuntimeError("upstream is down"))
    async with Client(build_server(executor)) as client:
        with pytest.raises(Exception, match="upstream is down"):
            await client.call_tool("get_text", {"reference": "Genesis 1:1"})


def test_every_public_schema_is_a_valid_json_schema():
    from jsonschema import Draft202012Validator

    for schema in tool_schemas.get_tools_for_surface("mcp"):
        Draft202012Validator.check_schema(schema["input_schema"])


@pytest.mark.parametrize(
    ("tool", "arguments", "message"),
    [
        ("get_text", {}, "'reference' is a required property"),
        ("get_text", {"reference": 5}, "reference: 5 is not of type 'string'"),
        (
            "specific_keyword_search",
            {"query": "x", "size": 10_000},
            "size: 10000 is greater than the maximum of 100",
        ),
    ],
)
async def test_invalid_arguments_get_clean_errors(executor, tool, arguments, message):
    """Without validation these surfaced as bare Python errors such as "'reference'"."""
    async with Client(build_server(executor)) as client:
        with pytest.raises(ToolError) as excinfo:
            await client.call_tool(tool, arguments)
    assert str(excinfo.value) == f"Invalid arguments for {tool}: {message}"
    executor.client.get_text.assert_not_awaited()


async def test_invalid_arguments_are_counted(executor):
    key = ("mcp_errors_total", {"tool_name": "get_text", "error_type": "InvalidArguments"})
    before = _sample(key[0], **key[1])
    async with Client(build_server(executor)) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_text", {})
    assert _sample(key[0], **key[1]) - before == 1


def _sample(name, **labels):
    return REGISTRY.get_sample_value(name, {"server": "chatbot-mcp", **labels}) or 0.0


async def test_tool_metrics_carry_the_server_label(executor):
    """Distinguishes this server's series from the old sefaria-mcp's during deprecation."""
    calls = ("mcp_tool_calls_total", {"tool_name": "get_text", "status": "success"})
    durations = ("mcp_tool_duration_seconds_count", {"tool_name": "get_text"})
    payloads = ("mcp_tool_payload_bytes_count", {"tool_name": "get_text"})
    before = [_sample(n, **lbl) for n, lbl in (calls, durations, payloads)]

    async with Client(build_server(executor)) as client:
        await client.call_tool("get_text", {"reference": "Genesis 1:1"})

    after = [_sample(n, **lbl) for n, lbl in (calls, durations, payloads)]
    assert [a - b for a, b in zip(after, before, strict=True)] == [1, 1, 1]


async def test_tool_error_metrics_carry_the_server_label(executor):
    executor.client.get_text = AsyncMock(side_effect=RuntimeError("upstream is down"))
    errors = ("mcp_errors_total", {"tool_name": "get_text", "error_type": "ToolError"})
    calls = ("mcp_tool_calls_total", {"tool_name": "get_text", "status": "error"})
    before = [_sample(n, **lbl) for n, lbl in (errors, calls)]

    async with Client(build_server(executor)) as client:
        with pytest.raises(ToolError, match="upstream is down"):
            await client.call_tool("get_text", {"reference": "Genesis 1:1"})

    after = [_sample(n, **lbl) for n, lbl in (errors, calls)]
    assert [a - b for a, b in zip(after, before, strict=True)] == [1, 1]


async def test_server_info_reports_the_release_tag(executor, monkeypatch):
    """The old server reported FastMCP's library version, so the wire never said what was live."""
    monkeypatch.setenv("SEFARIA_MCP_VERSION", "1.9.0-dev.3")
    r = await _initialize(executor, Host="chat.sefaria.org")
    data = next(line[len("data:") :] for line in r.text.splitlines() if line.startswith("data:"))
    assert json.loads(data)["result"]["serverInfo"]["version"] == "1.9.0-dev.3"


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
from fastmcp.exceptions import ToolError
from prometheus_client import REGISTRY
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
        r = await client.post(
            "/mcp/", json=INIT, headers={**MCP_HEADERS, "Host": "chat.sefaria.org"}
        )
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
        for path in (
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-authorization-server",
        ):
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


async def test_mcp_executors_never_send_the_sefaria_api_key(monkeypatch):
    """The public MCP process builds its own clients; none may carry SEFARIA_API_KEY."""
    import functools

    import httpx

    from chat.V2.agent import sefaria_client as sefaria_client_module
    from mcp_server.app import build_mcp_executor

    monkeypatch.setenv("SEFARIA_API_KEY", "sfr_test_fake_key")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    monkeypatch.setattr(
        sefaria_client_module.httpx,
        "AsyncClient",
        functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(handler)),
    )

    executor = build_mcp_executor()
    try:
        await executor.client._get_json("api/calendars")
    finally:
        await executor.client.close()

    assert len(seen) == 1
    assert "x-api-key" not in seen[0].headers
