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
from mcp_server.app import build_app, user_auth_settings

ISSUER = "https://chat.test"
RESOURCE = f"{ISSUER}/mcp/user"
ADMIN_RESOURCE = f"{ISSUER}/mcp/admin"
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
        if request.url.path == "/api/admin/topic-prompts":
            return httpx.Response(200, json={"results": [{"id": "p1"}], "next_cursor": None})
        if request.url.path == "/api/admin/topic-prompts/stale":
            return httpx.Response(
                409, json={"error": "Version conflict.", "current": {"version": "v2"}}
            )
        if request.url.path in ("/api/admin/topic-prompts/publish", "/api/admin/topic-prompts/generate"):
            return httpx.Response(200, json={"results": [], "task_ids": []})
        if request.url.path.startswith("/api/admin/topic-prompts/"):
            return httpx.Response(200, json={"result": {"id": "p1"}, "dry_run": False})
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
SHEET_TOOLS = {
    "search_user_source_sheets",
    "get_source_sheet",
    "create_source_sheet",
    "update_source_sheet",
}


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


ADMIN_TOOLS = {
    "list_topic_prompts",
    "update_topic_prompt",
    "publish_topic_prompts",
    "generate_topic_prompts",
    "get_admin_task",
    "tag_ref_with_topic",
    "untag_ref_from_topic",
    "get_lexicon_entry",
    "update_lexicon_entry",
    "rename_lexicon_headword",
    "get_version_metadata",
    "update_version_metadata",
    "find_versions",
    "check_index_dependencies",
    "purge_cache",
}


def admin_token(user_id: int = 42) -> str:
    return access_token(user_id=user_id, audience=ADMIN_RESOURCE, scope=access_tokens.ADMIN_SCOPE)


async def test_admin_endpoint_serves_only_the_topic_prompt_tools(executor):
    async with http(executor) as client:
        _, admin_tools = await rpc(client, "tools/list", {}, token=admin_token(), path="/mcp/admin")
        _, user_tools = await rpc(client, "tools/list", {}, token=access_token())
        metadata = await client.get(
            "/.well-known/oauth-protected-resource/mcp/admin", headers={"Host": "chat.test"}
        )
    tools = {t["name"]: t for t in admin_tools["result"]["tools"]}
    assert set(tools) == ADMIN_TOOLS
    assert tools["list_topic_prompts"]["annotations"]["readOnlyHint"] is True
    assert not ADMIN_TOOLS & {t["name"] for t in user_tools["result"]["tools"]}
    assert metadata.json()["resource"] == ADMIN_RESOURCE
    assert metadata.json()["scopes_supported"] == ["admin"]


async def test_tokens_only_work_on_their_own_endpoint(executor):
    async with http(executor) as client:
        user_at_admin, _ = await rpc(
            client, "initialize", INIT, token=access_token(), path="/mcp/admin"
        )
        admin_at_user, _ = await rpc(client, "initialize", INIT, token=admin_token())
    assert user_at_admin.status_code in (401, 403)
    assert admin_at_user.status_code in (401, 403)


async def test_admin_tools_call_sefaria_as_the_signed_in_user(executor, sefaria):
    async with http(executor) as client:
        _, listed = await rpc(
            client,
            "tools/call",
            {"name": "list_topic_prompts", "arguments": {"topic": ["shabbat"], "published": False}},
            token=admin_token(user_id=7),
            path="/mcp/admin",
        )
        _, updated = await rpc(
            client,
            "tools/call",
            {
                "name": "update_topic_prompt",
                "arguments": {"id": "p1", "lang": "en", "base_version": "v1", "prompt": "New"},
            },
            token=admin_token(user_id=7),
            path="/mcp/admin",
        )
    assert not listed["result"].get("isError") and not updated["result"].get("isError")
    get, patch = sefaria
    assert get.method == "GET" and get.url.path == "/api/admin/topic-prompts"
    assert get.url.params.get("topic") == "shabbat" and get.url.params.get("published") == "false"
    assert patch.method == "PATCH" and patch.url.path == "/api/admin/topic-prompts/p1"
    assert json.loads(patch.content) == {"lang": "en", "base_version": "v1", "prompt": "New"}
    for request in sefaria:
        sent = request.headers["X-Session-ID"]
        assert sefaria_tokens.user_id_from(sent, TOKEN_SECRET, aud=sefaria_tokens.API_AUDIENCE) == 7


async def test_a_version_conflict_shows_the_current_version(executor, sefaria):
    async with http(executor) as client:
        _, result = await rpc(
            client,
            "tools/call",
            {
                "name": "update_topic_prompt",
                "arguments": {"id": "stale", "lang": "en", "base_version": "v1", "prompt": "x"},
            },
            token=admin_token(),
            path="/mcp/admin",
        )
    assert result["result"]["isError"]
    error = json.loads(result["result"]["content"][0]["text"])["error"]
    assert "HTTP 409" in error and '"version": "v2"' in error


def test_admin_resource_defaults_beside_the_user_resource(monkeypatch):
    # The MCP host can differ from the issuer (as on the Coolify test app).
    monkeypatch.setenv("MCP_USER_RESOURCE_URL", "https://mcp.test/mcp/user")
    monkeypatch.delenv("MCP_ADMIN_RESOURCE_URL", raising=False)
    assert user_auth_settings()["admin_resource"] == "https://mcp.test/mcp/admin"


async def test_unknown_admin_tool_arguments_are_rejected_cleanly(executor, sefaria):
    async with http(executor) as client:
        _, result = await rpc(
            client,
            "tools/call",
            {"name": "list_topic_prompts", "arguments": {"q": "rest"}},
            token=admin_token(),
            path="/mcp/admin",
        )
    assert result["result"]["isError"]
    assert "Invalid arguments for list_topic_prompts" in result["result"]["content"][0]["text"]
    assert not sefaria


async def test_new_admin_tools_reach_their_staff_routes(executor, sefaria):
    calls = [
        ("publish_topic_prompts", {"items": [{"id": "p1", "base_version": "v1"}], "published": False}),
        ("generate_topic_prompts", {"topic": "shabbat", "ids": ["p1"]}),
        ("get_admin_task", {"task_id": "t/1"}),
        ("tag_ref_with_topic", {"ref": "Genesis 6:9", "topic": "noah", "dry_run": True}),
        ("untag_ref_from_topic", {"ref": "Genesis 6:9", "topic": "noah", "lang": "he"}),
    ]
    async with http(executor) as client:
        for name, arguments in calls:
            _, result = await rpc(
                client,
                "tools/call",
                {"name": name, "arguments": arguments},
                token=admin_token(user_id=7),
                path="/mcp/admin",
            )
            assert not result["result"].get("isError"), (name, result)
    publish, generate, task, tag, untag = sefaria
    assert (publish.method, publish.url.path) == ("POST", "/api/admin/topic-prompts/publish")
    assert json.loads(publish.content) == {
        "items": [{"id": "p1", "base_version": "v1"}],
        "lang": "en",
        "published": False,
        "dry_run": False,
    }
    assert (generate.method, generate.url.path) == ("POST", "/api/admin/topic-prompts/generate")
    assert json.loads(generate.content) == {"topic": "shabbat", "lang": "en", "ids": ["p1"]}
    assert (task.method, task.url.raw_path) == ("GET", b"/api/admin/tasks/t%2F1")
    assert (tag.method, tag.url.path) == ("POST", "/api/admin/ref-topic-links")
    assert json.loads(tag.content) == {"ref": "Genesis 6:9", "topic": "noah", "lang": "en", "dry_run": True}
    assert (untag.method, untag.url.path) == ("DELETE", "/api/admin/ref-topic-links")
    assert dict(untag.url.params) == {"ref": "Genesis 6:9", "topic": "noah", "lang": "he"}
    for request in sefaria:
        sent = request.headers["X-Session-ID"]
        assert sefaria_tokens.user_id_from(sent, TOKEN_SECRET, aud=sefaria_tokens.API_AUDIENCE) == 7


async def test_lexicon_tools_reach_their_staff_routes(executor, sefaria):
    calls = [
        ("get_lexicon_entry", {"lexicon": "Jastrow Dictionary", "headword": "אב"}),
        (
            "update_lexicon_entry",
            {"lexicon": "Jastrow Dictionary", "headword": "אב", "content": {"notes": "x"}, "base_version": "v1"},
        ),
        (
            "rename_lexicon_headword",
            {"lexicon": "Jastrow Dictionary", "headword": "אב", "new_headword": "אָב", "dry_run": True},
        ),
    ]
    async with http(executor) as client:
        for name, arguments in calls:
            _, result = await rpc(
                client, "tools/call", {"name": name, "arguments": arguments}, token=admin_token(), path="/mcp/admin"
            )
            assert not result["result"].get("isError"), (name, result)
    get, update, rename = sefaria
    path = "/api/admin/lexicon-entries/Jastrow%20Dictionary/%D7%90%D7%91"
    assert (get.method, get.url.raw_path) == ("GET", path.encode())
    assert (update.method, update.url.raw_path) == ("PATCH", path.encode())
    assert json.loads(update.content) == {"content": {"notes": "x"}, "base_version": "v1", "dry_run": False}
    assert (rename.method, rename.url.raw_path) == (
        "PATCH",
        b"/api/admin/lexicon-entries/headword/Jastrow%20Dictionary/%D7%90%D7%91",
    )
    assert json.loads(rename.content) == {"new_headword": "אָב", "dry_run": True}


async def test_version_and_cache_tools_reach_their_staff_routes(executor, sefaria):
    calls = [
        ("get_version_metadata", {"title": "Genesis", "lang": "en", "version_title": "The Koren Jerusalem Bible"}),
        (
            "update_version_metadata",
            {
                "title": "Genesis",
                "lang": "en",
                "version_title": "The Koren Jerusalem Bible",
                "fields": {"status": "locked"},
                "base_version": "v1",
                "dry_run": True,
            },
        ),
        ("find_versions", {"version_title": "The Koren Jerusalem Bible", "language": "en"}),
        ("check_index_dependencies", {"title": "Genesis"}),
        ("purge_cache", {"ref": "Genesis 1"}),
    ]
    async with http(executor) as client:
        for name, arguments in calls:
            _, result = await rpc(
                client, "tools/call", {"name": name, "arguments": arguments}, token=admin_token(), path="/mcp/admin"
            )
            assert not result["result"].get("isError"), (name, result)
    get, update, find, deps, purge = sefaria
    path = b"/api/admin/versions/Genesis/en/The%20Koren%20Jerusalem%20Bible"
    assert (get.method, get.url.raw_path) == ("GET", path)
    assert (update.method, update.url.raw_path) == ("PATCH", path)
    assert json.loads(update.content) == {"fields": {"status": "locked"}, "base_version": "v1", "dry_run": True}
    assert (find.method, find.url.path) == ("GET", "/api/admin/versions")
    assert dict(find.url.params) == {"versionTitle": "The Koren Jerusalem Bible", "language": "en"}
    assert (deps.method, deps.url.path) == ("GET", "/api/admin/index-dependencies/Genesis")
    assert (purge.method, purge.url.path) == ("POST", "/api/admin/cache/purge")
    assert json.loads(purge.content) == {"ref": "Genesis 1"}

