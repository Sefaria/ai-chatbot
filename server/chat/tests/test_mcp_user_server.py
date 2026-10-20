"""The signed-in MCP endpoint, /mcp/user: OAuth resource server plus per-user sheet tools.

Access tokens are minted here the way the Django authorization server (mcp_auth) mints
them, and Sefaria is replaced by an httpx MockTransport that records what it receives.
"""

from __future__ import annotations

import asyncio
import functools
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from chat.V2.agent import sefaria_client as sefaria_client_module
from chat.V2.agent import tool_schemas
from chat.V2.agent.tool_executor import SefariaToolExecutor
from mcp_auth import access_tokens, sefaria_tokens
from mcp_server.app import build_app

ISSUER = "https://chat.test"
RESOURCE = f"{ISSUER}/mcp/user"
TOKEN_SECRET = "test-mcp-token-secret"
SIGNING_KEY = "test-signing-key-" + "x" * 32
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-06-18",
    "Host": "chat.test",
}


@pytest.fixture(autouse=True)
def user_auth_env(monkeypatch):
    monkeypatch.setenv("MCP_TOKEN_SECRET", TOKEN_SECRET)
    monkeypatch.setenv("MCP_OAUTH_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("MCP_OAUTH_ISSUER", ISSUER)
    monkeypatch.setenv("SEFARIA_MCP_ALLOWED_HOSTS", "chat.test")


@pytest.fixture
def executor():
    client = MagicMock()
    client.get_text = AsyncMock(return_value={"versions": [{"text": "In the beginning"}]})
    return SefariaToolExecutor(client=client)


@pytest.fixture
def sefaria(monkeypatch):
    """Stand-in for the Sefaria API; records each request the per-user clients make."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.startswith("/api/sheets/user/"):
            return httpx.Response(200, json={"sheets": [{"id": 1, "title": "Mine"}]})
        return httpx.Response(200, json={})

    monkeypatch.setattr(
        sefaria_client_module.httpx,
        "AsyncClient",
        functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(handler)),
    )
    return seen


def access_token(user_id: int = 42, **overrides) -> str:
    options = {
        "signing_key": SIGNING_KEY,
        "issuer": ISSUER,
        "audience": RESOURCE,
        "grant_id": "grant-1",
        "sefaria_user_id": user_id,
        "client_id": "client-1",
        **overrides,
    }
    return access_tokens.issue(**options)


@asynccontextmanager
async def http(executor):
    app = build_app(executor)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://chat.test") as client:
            yield client


async def rpc(client, method, params=None, token=None, path="/mcp/user"):
    headers = dict(HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    response = await client.post(path, json=body, headers=headers)
    if response.status_code != 200:
        return response, None
    data = next(line[5:] for line in response.text.splitlines() if line.startswith("data:"))
    return response, json.loads(data)


INIT = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "test", "version": "1"},
}
SHEET_TOOLS = {"search_user_source_sheets", "get_source_sheet", "create_source_sheet"}


async def test_not_served_without_sign_in_configured(executor, monkeypatch):
    monkeypatch.delenv("MCP_OAUTH_SIGNING_KEY")
    async with http(executor) as client:
        response, _ = await rpc(client, "initialize", INIT)
    assert response.status_code == 404


async def test_unauthenticated_requests_get_the_oauth_challenge(executor):
    async with http(executor) as client:
        response, _ = await rpc(client, "initialize", INIT)
        metadata = await client.get(
            "/.well-known/oauth-protected-resource/mcp/user", headers={"Host": "chat.test"}
        )
    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert (
        f'resource_metadata="{ISSUER}/.well-known/oauth-protected-resource/mcp/user"' in challenge
    )
    assert 'scope="sheets"' in challenge
    assert metadata.json()["resource"] == RESOURCE
    assert metadata.json()["authorization_servers"] == [ISSUER]
    assert metadata.json()["scopes_supported"] == ["sheets"]


@pytest.mark.parametrize(
    "token",
    [
        access_token(audience=f"{ISSUER}/mcp"),
        access_token(issuer="https://evil.test"),
        access_token(signing_key="another-key-" + "y" * 32),
        access_token(ttl_seconds=-60),
        access_token(scope="other"),
        "not-a-jwt",
    ],
)
async def test_bad_access_tokens_are_rejected(executor, token):
    async with http(executor) as client:
        response, _ = await rpc(client, "initialize", INIT, token=token)
    assert response.status_code in (401, 403)


async def test_sheet_tools_only_on_the_signed_in_endpoint(executor):
    async with http(executor) as client:
        _, user_tools = await rpc(client, "tools/list", {}, token=access_token())
        _, public_tools = await rpc(client, "tools/list", {}, path="/mcp")
    user_names = {t["name"] for t in user_tools["result"]["tools"]}
    public_names = {t["name"] for t in public_tools["result"]["tools"]}
    assert SHEET_TOOLS <= user_names
    assert not SHEET_TOOLS & public_names
    assert user_names == public_names | SHEET_TOOLS
    assert {t["name"] for t in tool_schemas.get_tools_for_surface("mcp_user")} == SHEET_TOOLS


async def test_public_tools_still_work_when_signed_in(executor):
    async with http(executor) as client:
        _, result = await rpc(
            client,
            "tools/call",
            {"name": "get_text", "arguments": {"reference": "Genesis 1:1"}},
            token=access_token(),
        )
    assert "In the beginning" in result["result"]["content"][0]["text"]


async def test_sheet_tools_send_a_fresh_mcp_token_for_the_signed_in_user(executor, sefaria):
    async with http(executor) as client:
        _, result = await rpc(
            client,
            "tools/call",
            {"name": "search_user_source_sheets", "arguments": {}},
            token=access_token(user_id=42),
        )
    assert not result["result"].get("isError"), result
    [request] = sefaria
    assert request.url.path == "/api/sheets/user/42/date/0/0"
    sent = request.headers["X-Session-ID"]
    assert sefaria_tokens.user_id_from(sent, TOKEN_SECRET, aud=sefaria_tokens.API_AUDIENCE) == 42
    # Scoped to API use: it is not a login token, and the widget's secret cannot read it.
    assert (
        sefaria_tokens.user_id_from(sent, TOKEN_SECRET, aud=sefaria_tokens.LOGIN_AUDIENCE) is None
    )


async def test_concurrent_users_never_share_a_session(executor, sefaria):
    async with http(executor) as client:
        await asyncio.gather(
            *(
                rpc(
                    client,
                    "tools/call",
                    {"name": "search_user_source_sheets", "arguments": {}},
                    token=access_token(user_id=user_id),
                )
                for user_id in range(1, 9)
            )
        )
    for request in sefaria:
        path_user = int(request.url.path.split("/")[4])
        token_user = sefaria_tokens.user_id_from(
            request.headers["X-Session-ID"], TOKEN_SECRET, aud=sefaria_tokens.API_AUDIENCE
        )
        assert path_user == token_user
    assert len({r.url.path for r in sefaria}) == 8
