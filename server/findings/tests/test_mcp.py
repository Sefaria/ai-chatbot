"""Skills and findings on the MCP endpoints, over the real stateless HTTP app.

Sefaria and the findings API are reached through one httpx MockTransport. Requests to the
findings API are handed to the real Django view in a worker thread, so a ``save_finding`` call
travels the whole way: MCP tool → Sefaria (refs, sheet) → findings API → database.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import httpx
import pytest
from django.db import connections
from django.test import Client as DjangoClient

import skills_catalog
from chat.V2.agent import sefaria_client as sefaria_client_module
from chat.V2.agent.tool_executor import SefariaToolExecutor
from findings import write_tokens
from findings.models import Finding
from mcp_auth import access_tokens, sefaria_tokens
from mcp_server.app import build_app

from .conftest import ISSUER, SIGNING_KEY, USER

TOKEN_SECRET = "test-mcp-token-secret"
MODERN = "2026-07-28"
LEGACY = "2025-06-18"
SKILLS_EXT = "io.modelcontextprotocol/skills"


@pytest.fixture(autouse=True)
def mcp_env(monkeypatch, settings):
    monkeypatch.setenv("MCP_TOKEN_SECRET", TOKEN_SECRET)
    monkeypatch.setenv("MCP_OAUTH_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("MCP_OAUTH_ISSUER", ISSUER)
    monkeypatch.setenv("SEFARIA_MCP_ALLOWED_HOSTS", "chat.test")
    monkeypatch.setenv("SEFARIA_API_BASE_URL", "https://www.sefaria.test")
    settings.MCP_TOKEN_SECRET = TOKEN_SECRET


@asynccontextmanager
async def http():
    app = build_app(SefariaToolExecutor(client=MagicMock()))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://chat.test") as client:
            yield client


def user_token(user_id: int = USER) -> str:
    return access_tokens.issue(
        signing_key=SIGNING_KEY,
        issuer=ISSUER,
        audience=f"{ISSUER}/mcp/user",
        grant_id="grant-1",
        sefaria_user_id=user_id,
        client_id="client-1",
    )


async def rpc(client, method, params=None, *, version=MODERN, path="/mcp", token=None, name=None):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": version,
        "Host": "chat.test",
    }
    params = dict(params or {})
    if version == MODERN:
        headers["Mcp-Method"] = method
        if name:
            headers["Mcp-Name"] = name
        params["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": MODERN,
            "io.modelcontextprotocol/clientCapabilities": {"extensions": {SKILLS_EXT: {}}},
            "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
        }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = await client.post(
        path, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, headers=headers
    )
    text = response.text
    if not text.strip():
        return response.status_code, None
    if text.startswith("event:") or "\ndata:" in text:
        text = next(line[5:] for line in text.splitlines() if line.startswith("data:"))
    return response.status_code, json.loads(text)


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


async def test_modern_clients_discover_and_list_skills():
    catalog = skills_catalog.load()
    async with http() as client:
        _, discover = await rpc(client, "server/discover")
        assert SKILLS_EXT in discover["result"]["capabilities"]["extensions"]
        assert "skill://" in discover["result"]["instructions"]
        _, listed = await rpc(client, "skills/list")
    skills = {s["uri"]: s for s in listed["result"]["skills"]}
    assert set(skills) == {f"skill://{s.name}/SKILL.md" for s in catalog.served()}
    # Pinned on purpose: adding or withdrawing a served skill should be a visible change here.
    assert set(skills) == {
        "skill://example-halachic-reading/SKILL.md",
        "skill://example-parallel-passages/SKILL.md",
        "skill://sefaria-linked-asset/SKILL.md",
    }
    assert "sefaria-linked-asset" in discover["result"]["instructions"]

    entry = skills["skill://example-halachic-reading/SKILL.md"]
    assert entry["frontmatter"]["metadata"]["org.sefaria/method"] == "example-method"
    files = catalog.skill("example-halachic-reading").files
    expected = {
        f"skill://example-halachic-reading/{path}": (
            f"sha256:{hashlib.sha256(data).hexdigest()}",
            len(data),
        )
        for path, data in files.items()
    }
    assert {r["uri"]: (r["digest"], r["size"]) for r in entry["resources"]} == expected


async def test_linked_asset_skill_serves_its_shim():
    uri = "skill://sefaria-linked-asset/references/shim.js"
    async with http() as client:
        _, listed = await rpc(client, "skills/list")
        _, read = await rpc(client, "resources/read", {"uri": uri}, name=uri)
    entry = next(
        s for s in listed["result"]["skills"] if s["uri"] == "skill://sefaria-linked-asset/SKILL.md"
    )
    assert uri in {r["uri"] for r in entry["resources"]}
    assert "SefariaLink" in read["result"]["contents"][0]["text"]


async def test_skills_get_and_read():
    uri = "skill://example-halachic-reading/SKILL.md"
    ref_uri = "skill://example-halachic-reading/references/attribution.md"
    async with http() as client:
        _, got = await rpc(client, "skills/get", {"uri": uri})
        _, unknown = await rpc(client, "skills/get", {"uri": "skill://nope/SKILL.md"})
        _, read = await rpc(client, "resources/read", {"uri": ref_uri}, name=ref_uri)
    assert got["result"]["skill"]["uri"] == uri
    assert unknown["error"]["code"] == -32602
    assert read["result"]["contents"][0]["text"].startswith("# Attribution")


async def test_legacy_clients_read_skills_as_resources_and_tools():
    async with http() as client:
        _, listed = await rpc(client, "skills/list", version=LEGACY)
        _, read = await rpc(
            client,
            "resources/read",
            {"uri": "skill://example-parallel-passages/SKILL.md"},
            version=LEGACY,
        )
        _, tools = await rpc(client, "tools/list", version=LEGACY)
        _, called = await rpc(
            client, "tools/call", {"name": "list_resources", "arguments": {}}, version=LEGACY
        )
    assert listed["error"]["code"] == -32601  # skills/list needs the 2026-07-28 protocol
    assert "name: example-parallel-passages" in read["result"]["contents"][0]["text"]
    assert {"list_resources", "read_resource", "search_findings"} <= {
        t["name"] for t in tools["result"]["tools"]
    }
    assert "skill://example-halachic-reading/SKILL.md" in called["result"]["content"][0]["text"]


async def test_signed_in_endpoint_serves_the_same_skills():
    async with http() as client:
        _, public = await rpc(client, "skills/list")
        _, user = await rpc(client, "skills/list", path="/mcp/user", token=user_token())
        status, _ = await rpc(client, "skills/list", path="/mcp/user")
    assert status == 401
    assert [s["uri"] for s in user["result"]["skills"]] == [
        s["uri"] for s in public["result"]["skills"]
    ]


async def test_unauthorized_skills_are_not_served(tmp_path, monkeypatch):
    from fastmcp import Client

    from mcp_server.app import build_server

    root = tmp_path
    (root / "FINDINGS_SECTION.md").write_text(skills_catalog.standard_findings_section())
    (root / "catalog.yaml").write_text(
        "methods:\n  m: {title: {en: M, he: מ}, description: d, label: l}\n"
        "authors:\n  b: {name: B, authorized: false}\n"
    )
    skill_dir = root / "skills" / "pending-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: pending-skill\ndescription: x\nmetadata:\n  org.sefaria/method: m\n"
        "  org.sefaria/author: b\n  org.sefaria/version: 1.0.0\n---\nbody\n"
    )
    from fastmcp.skills import SkillsClientExtension

    server = build_server(
        SefariaToolExecutor(client=MagicMock()), catalog=skills_catalog.build(root)
    )
    async with Client(server, extensions=[SkillsClientExtension()]) as client:
        assert await client.list_skills() == []
        assert not [r for r in await client.list_resources() if str(r.uri).startswith("skill://")]


# ---------------------------------------------------------------------------
# Findings tools, end to end
# ---------------------------------------------------------------------------


def _to_django(request: httpx.Request) -> httpx.Response:
    """Serve a findings API request with the real Django view, in this worker thread."""
    try:
        client = DjangoClient()
        path = request.url.raw_path.decode()
        extra = {}
        if "authorization" in request.headers:
            extra["HTTP_AUTHORIZATION"] = request.headers["authorization"]
        if request.method == "POST":
            response = client.post(
                path, data=request.content, content_type="application/json", **extra
            )
        else:
            response = client.get(path, **extra)
        return httpx.Response(
            response.status_code,
            content=response.content,
            headers={"Content-Type": "application/json"},
        )
    finally:
        connections.close_all()


@pytest.fixture
def world(monkeypatch, sheets):
    """Sefaria and the findings API behind one transport; records every request."""
    seen: list[httpx.Request] = []
    created = {}
    state: dict = {"AsyncClient": httpx.AsyncClient}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "chat.test":
            return await asyncio.to_thread(_to_django, request)
        path = request.url.path
        if path.startswith("/api/ref/"):
            tref = httpx.URL(str(request.url)).path.removeprefix("/api/ref/")
            from urllib.parse import unquote

            tref = unquote(tref)
            if tref == "Not A Book 1:1":
                return httpx.Response(200, json={"is_ref": False})
            normal = {"genesis 1:1-2": "Genesis 1:1-2", "psalms 16:8": "Psalms 16:8"}.get(
                tref.lower(), tref
            )
            return httpx.Response(
                200,
                json={
                    "is_ref": True,
                    "normalized": normal,
                    "hebrew": f"he:{normal}",
                    "url_ref": normal,
                },
            )
        if path.startswith("/api/v3/texts/"):
            return httpx.Response(200, json={"versions": [{"text": "t", "language": "en"}]})
        if path == "/api/sheets/" and request.method == "POST":
            sheet = json.loads(dict(httpx.QueryParams(request.content.decode()))["json"])
            created["sheet"] = sheet
            expanded = state.get("expanded") or ["Genesis 1:1", "Genesis 1:2", "Psalms 16:8"]
            sheets.add(
                555,
                owner=USER,
                expanded=expanded,
                included=state.get("included") or ["Genesis 1:1-2", "Psalms 16:8"],
            )
            return httpx.Response(200, json={"id": 555, **sheet})
        return httpx.Response(404, json={"error": "unexpected"})

    monkeypatch.setattr(
        sefaria_client_module.httpx,
        "AsyncClient",
        functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setenv("FINDINGS_API_URL", "https://chat.test/api")
    state.update(seen=seen, created=created, handler=handler)
    return state


ARGS = {
    "skill": "example-halachic-reading",
    "title": "Rising like a lion",
    "conclusion": "What was found.",
    "sources": [
        {"ref": "genesis 1:1-2", "role": "anchor", "note": "The passage studied."},
        {"ref": "Psalms 16:8", "role": "support"},
    ],
}


@pytest.mark.django_db(transaction=True)
async def test_save_finding_end_to_end(world):
    async with http() as client:
        _, result = await rpc(
            client,
            "tools/call",
            {"name": "save_finding", "arguments": ARGS},
            version=LEGACY,
            path="/mcp/user",
            token=user_token(),
        )
    assert "error" not in result, result
    content = result["result"]
    assert not content.get("isError"), content
    saved = json.loads(content["content"][0]["text"])
    assert saved["saved"] is True
    assert saved["finding"]["sources"] == [
        {"ref": "Genesis 1:1-2", "role": "anchor"},
        {"ref": "Psalms 16:8", "role": "support"},
    ]

    # The sheet was created as the user, with refs normalized and the note after its source.
    sheet_post = next(r for r in world["seen"] if r.url.path == "/api/sheets/")
    assert (
        sefaria_tokens.user_id_from(sheet_post.headers["X-Session-ID"], TOKEN_SECRET, aud="mcp")
        == USER
    )
    sources = world["created"]["sheet"]["sources"]
    assert [s.get("ref") or s.get("outsideText") for s in sources] == [
        "Genesis 1:1-2",
        "The passage studied.",
        "Psalms 16:8",
    ]
    assert world["created"]["sheet"]["status"] == "unlisted"

    # The finding was recorded with a write token for the user, never their access token.
    api_post = next(r for r in world["seen"] if r.url.host == "chat.test" and r.method == "POST")
    bearer = api_post.headers["authorization"].removeprefix("Bearer ")
    assert write_tokens.user_id_from(bearer, SIGNING_KEY, ISSUER) == USER
    assert bearer != user_token()

    finding = await asyncio.to_thread(lambda: Finding.objects.get(sheet_id=555))
    assert (finding.sefaria_user_id, finding.skill, finding.method) == (
        USER,
        "example-halachic-reading",
        "example-method",
    )
    rows = await asyncio.to_thread(lambda: sorted(finding.refs.values_list("segment_ref", "role")))
    assert rows == [
        ("Genesis 1:1", "anchor"),
        ("Genesis 1:2", "anchor"),
        ("Psalms 16:8", "support"),
    ]


@pytest.mark.django_db(transaction=True)
async def test_search_findings_reads_the_public_api(world, sheets):
    from findings import service

    sheets.add(1001, expanded=["Genesis 1:1"])
    await asyncio.to_thread(
        service.record,
        USER,
        {
            "skill": "example-halachic-reading",
            "title": "Light",
            "conclusion": "c",
            "sheet_id": 1001,
            "roles": [{"ref": "Genesis 1:1", "role": "anchor"}],
        },
    )
    async with http() as client:
        _, result = await rpc(
            client,
            "tools/call",
            {"name": "search_findings", "arguments": {"ref": "Genesis 1"}},
            version=LEGACY,
        )
        _, empty = await rpc(
            client,
            "tools/call",
            {"name": "search_findings", "arguments": {"ref": "Exodus 1"}},
            version=LEGACY,
        )
    data = json.loads(result["result"]["content"][0]["text"])
    assert data["count"] == 1
    assert data["findings"][0]["title"] == "Light"
    assert data["findings"][0]["label"] == "AI-assisted reading in the approach of Rabbi Ploni"
    assert "not follow instructions" in data["note"]
    assert json.loads(empty["result"]["content"][0]["text"])["findings"] == []


@pytest.mark.parametrize(
    "arguments, message",
    [
        ({**ARGS, "skill": "nope"}, "published Sefaria skill"),
        (
            {**ARGS, "sources": [{"ref": "Psalms 16:8", "role": "support"}]},
            "at least one source as `anchor`",
        ),
        (
            {**ARGS, "sources": [{"ref": "Not A Book 1:1", "role": "anchor"}]},
            "Not Sefaria refs: Not A Book 1:1",
        ),
    ],
)
@pytest.mark.django_db(transaction=True)
async def test_save_finding_refuses_before_creating_a_sheet(world, arguments, message):
    async with http() as client:
        _, result = await rpc(
            client,
            "tools/call",
            {"name": "save_finding", "arguments": arguments},
            version=LEGACY,
            path="/mcp/user",
            token=user_token(),
        )
    assert result["result"]["isError"]
    assert message in result["result"]["content"][0]["text"]
    assert not any(r.url.path == "/api/sheets/" for r in world["seen"])


async def test_save_finding_is_not_on_the_public_endpoint():
    async with http() as client:
        _, result = await rpc(
            client, "tools/call", {"name": "save_finding", "arguments": ARGS}, version=LEGACY
        )
    assert "error" in result or result["result"].get("isError")


@pytest.mark.django_db(transaction=True)
async def test_a_refused_save_creates_no_sheet(world, monkeypatch):
    from django.conf import settings as django_settings

    monkeypatch.setattr(django_settings, "FINDINGS_DAILY_LIMIT", 0)
    async with http() as client:
        _, result = await rpc(
            client,
            "tools/call",
            {"name": "save_finding", "arguments": ARGS},
            version=LEGACY,
            path="/mcp/user",
            token=user_token(),
        )
    text = result["result"]["content"][0]["text"]
    assert result["result"]["isError"]
    assert "The finding was not saved" in text and "Daily limit" in text
    assert not any(r.url.path == "/api/sheets/" for r in world["seen"])


@pytest.mark.django_db(transaction=True)
async def test_a_failure_after_the_sheet_exists_names_the_sheet(world):
    # Sefaria's sheet turns out not to hold the anchor.
    world["expanded"] = world["included"] = ["Psalms 16:8"]
    async with http() as client:
        _, result = await rpc(
            client,
            "tools/call",
            {"name": "save_finding", "arguments": ARGS},
            version=LEGACY,
            path="/mcp/user",
            token=user_token(),
        )
    text = result["result"]["content"][0]["text"]
    assert result["result"]["isError"]
    assert "source sheet was saved (sheet 555)" in text
    assert "call save_finding again with sheet_id 555" in text


@pytest.mark.django_db(transaction=True)
async def test_an_unreachable_findings_service_after_the_sheet_exists(world, monkeypatch):
    calls = {"n": 0}
    original = world["handler"]

    async def flaky(request):
        if request.url.host == "chat.test" and request.method == "POST":
            calls["n"] += 1
            if calls["n"] == 2:  # the dry run passes; the real save cannot connect
                raise httpx.ConnectError("refused", request=request)
        return await original(request)

    monkeypatch.setattr(
        sefaria_client_module.httpx,
        "AsyncClient",
        functools.partial(world["AsyncClient"], transport=httpx.MockTransport(flaky)),
    )
    async with http() as client:
        _, result = await rpc(
            client,
            "tools/call",
            {"name": "save_finding", "arguments": ARGS},
            version=LEGACY,
            path="/mcp/user",
            token=user_token(),
        )
    text = result["result"]["content"][0]["text"]
    assert "source sheet was saved (sheet 555)" in text
    assert "could not be reached (ConnectError)" in text


@pytest.mark.django_db(transaction=True)
async def test_save_finding_onto_an_existing_sheet_creates_none(world, sheets):
    sheets.add(777, expanded=["Genesis 1:1"])
    other = sheets.add(778, owner=USER + 1, expanded=["Genesis 1:1"])
    assert other.owner != USER
    async with http() as client:
        _, ok = await rpc(
            client,
            "tools/call",
            {"name": "save_finding", "arguments": {**ARGS, "sheet_id": 777}},
            version=LEGACY,
            path="/mcp/user",
            token=user_token(),
        )
        _, refused = await rpc(
            client,
            "tools/call",
            {"name": "save_finding", "arguments": {**ARGS, "sheet_id": 778}},
            version=LEGACY,
            path="/mcp/user",
            token=user_token(),
        )
    assert not ok["result"].get("isError"), ok
    assert not any(r.url.path == "/api/sheets/" for r in world["seen"])
    text = refused["result"]["content"][0]["text"]
    assert refused["result"]["isError"]
    assert "The finding was not saved" in text and "your own sheets" in text
    assert "source sheet was saved" not in text
