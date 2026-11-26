"""SefariaClient.mint_social_token: the mcp token traded at Sefaria's POST /api/social/token."""

from __future__ import annotations

import functools

import httpx
import pytest

from chat.V2.agent import sefaria_client as sefaria_client_module
from chat.V2.agent.sefaria_client import SefariaClient


def client_answering(monkeypatch, status, body=None, raises=None):
    seen = []

    def handler(request):
        seen.append(request)
        if raises:
            raise raises
        return httpx.Response(status, json=body or {})

    monkeypatch.setattr(
        sefaria_client_module.httpx,
        "AsyncClient",
        functools.partial(httpx.AsyncClient, transport=httpx.MockTransport(handler)),
    )
    client = SefariaClient(base_url="https://www.sefaria.test")
    client.set_user_session("42", "mcp-token")
    return client, seen


async def test_returns_the_token(monkeypatch):
    client, seen = client_answering(monkeypatch, 200, {"token": "t", "expires_at": 1})
    assert await client.mint_social_token() == "t"
    [request] = seen
    assert request.method == "POST"
    assert str(request.url) == "https://www.sefaria.test/api/social/token"
    assert request.headers["X-Session-ID"] == "mcp-token"
    await client.close()


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, "Try again in a minute"),
        (503, "not set up on this Sefaria site"),
        (401, "Reconnect the Sefaria connector"),
        (403, "HTTP 403"),  # a Sefaria without the MCP route answers with a CSRF refusal
        (200, "did not sign you in"),  # 200 without a token
    ],
)
async def test_refusals_say_what_to_do(monkeypatch, status, expected):
    client, _ = client_answering(monkeypatch, status, {})
    with pytest.raises(ValueError, match=expected):
        await client.mint_social_token()
    await client.close()


async def test_unreachable(monkeypatch):
    client, _ = client_answering(monkeypatch, 0, raises=httpx.ConnectError("down"))
    with pytest.raises(ValueError, match="could not be reached"):
        await client.mint_social_token()


async def test_needs_a_user_session():
    with pytest.raises(ValueError, match="requires authenticated user context"):
        await SefariaClient(base_url="https://www.sefaria.test").mint_social_token()
