"""save_asset against a fake Sefaria Social (httpx MockTransport), coded to sefaria-social spec 060.

The Sefaria side (minting the Social token) is a stub client here; the MCP end-to-end test in
chat/tests/test_mcp_user_server.py runs the real token trade.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from claude_assets import tools

SOCIAL = "https://social.test"
TOKEN = "social-identity-token-SECRET"
HTML = (
    "<!doctype html><title>x</title>"
    '<script type="application/json" id="sefaria-asset">'
    '{"v":1,"title":"The Seven Days","refs":[{"ref":"Genesis 1:1-5","label":"Day one"}]}'
    "</script><p>In the beginning</p>"
)
SAVED = {
    "asset": {
        "id": "4f1c",
        "title": "The Seven Days",
        "manifest": {"title": "The Seven Days", "refs": [{"ref": "Genesis 1:1-5"}]},
        "versions": [{"id": "v2", "n": 2, "contentUrl": "https://content.test/v2"}],
    },
    "version": {"id": "v2", "n": 2, "sha256": "abc", "bytes": 10},
    "created": True,
    "unchanged": False,
    "firstRef": "Genesis 1:1-5",
}


class FakeSefaria:
    user_id = "42"

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.minted = 0

    async def mint_social_token(self) -> str:
        self.minted += 1
        if self.error:
            raise self.error
        return TOKEN


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("SOCIAL_API_BASE_URL", SOCIAL + "/")
    monkeypatch.setenv("SEFARIA_WEB_URL", "https://www.sefaria.test")


def social(dry=(200, {"dryRun": True, "firstRef": "Genesis 1:1-5"}), save=(201, SAVED)):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status, body = dry if json.loads(request.content).get("dryRun") else save
        return httpx.Response(status, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


async def test_dry_run_then_save_and_links():
    http, seen = social()
    result = await tools.save_asset(
        FakeSefaria(),
        {"html": HTML, "asset_id": "4f1c", "source_url": "https://claude.ai/public/artifacts/x"},
        http=http,
    )
    dry, save = seen
    for request in seen:
        assert str(request.url) == f"{SOCIAL}/api/v1/claude-assets"
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert json.loads(dry.content) == {
        "html": HTML,
        "assetId": "4f1c",
        "sourceUrl": "https://claude.ai/public/artifacts/x",
        "dryRun": True,
    }
    assert "dryRun" not in json.loads(save.content)
    assert result["assetUrl"] == "https://content.test/v2"
    assert result["readerUrl"] == "https://www.sefaria.test/Genesis.1.1-5?a2=4f1c@v2&link=2-1"
    assert result["asset"] == {
        "id": "4f1c",
        "title": "The Seven Days",
        "versionId": "v2",
        "version": 2,
        "refs": ["Genesis 1:1-5"],
    }
    assert "version 2" in result["message"] and "verified profile" in result["message"]


async def test_refused_file_is_not_saved_and_every_problem_comes_back():
    refusal = {
        "error": {"code": "asset.invalid", "message": "The file cannot be saved."},
        "problems": [
            {
                "code": "ref.invalid",
                "message": "'Genesis 1:99' does not exist",
                "ref": "Genesis 1:99",
            },
            {"code": "manifest.invalid", "message": "title is required"},
        ],
    }
    http, seen = social(dry=(422, refusal))
    with pytest.raises(ValueError) as error:
        await tools.save_asset(FakeSefaria(), {"html": HTML}, http=http)
    text = str(error.value)
    assert len(seen) == 1  # the dry run only
    assert "The file cannot be saved. (asset.invalid)" in text
    assert "- [ref.invalid] 'Genesis 1:99' does not exist (ref: Genesis 1:99)" in text
    assert "- [manifest.invalid] title is required" in text
    assert "call save_asset again" in text


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (
            429,
            {"error": {"code": "asset.daily_limit", "message": "Daily limit reached."}},
            "tomorrow",
        ),
        (503, {"error": {"code": "asset.refs_unavailable", "message": "No refs."}}, "few minutes"),
        (
            404,
            {"error": {"code": "asset.not_found", "message": "No such asset."}},
            "Leave out asset_id",
        ),
        (
            403,
            {"error": {"code": "auth.mcp_not_allowed", "message": "Not here."}},
            "only save assets",
        ),
        (401, {"error": {"code": "auth.invalid_token", "message": "Bad token."}}, "Reconnect"),
        (500, "upstream exploded", "upstream exploded"),
    ],
)
async def test_social_errors_are_passed_through_as_actionable_text(status, body, expected):
    http, _ = social(dry=(status, body))
    with pytest.raises(ValueError, match=expected):
        await tools.save_asset(FakeSefaria(), {"html": HTML}, http=http)


async def test_failure_on_the_real_save_after_a_good_dry_run():
    http, seen = social(save=(429, {"error": {"code": "asset.daily_limit", "message": "Limit."}}))
    with pytest.raises(
        ValueError, match="The asset was not saved:\nLimit. \\(asset.daily_limit\\)"
    ):
        await tools.save_asset(FakeSefaria(), {"html": HTML}, http=http)
    assert len(seen) == 2


async def test_unchanged_file():
    http, _ = social(save=(200, {**SAVED, "created": False, "unchanged": True}))
    result = await tools.save_asset(FakeSefaria(), {"html": HTML}, http=http)
    assert result["unchanged"] is True
    assert "unchanged" in result["message"]


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"html": "  "}, "whole HTML file"),
        ({"html": "<p>no manifest</p>"}, "no manifest"),
        ({"html": HTML + "x" * (5 * 1024 * 1024)}, "Make it smaller"),
        ({"html": HTML, "source_url": "http://claude.ai/x"}, "must be an https link"),
    ],
)
async def test_checked_before_anything_is_sent(arguments, expected):
    sefaria = FakeSefaria()
    http, seen = social()
    with pytest.raises(ValueError, match=expected):
        await tools.save_asset(sefaria, arguments, http=http)
    assert seen == [] and sefaria.minted == 0


async def test_not_configured(monkeypatch):
    monkeypatch.delenv("SOCIAL_API_BASE_URL")
    with pytest.raises(ValueError, match="not set up on this server"):
        await tools.save_asset(FakeSefaria(), {"html": HTML})


async def test_needs_a_signed_in_user():
    sefaria = FakeSefaria()
    sefaria.user_id = None
    with pytest.raises(ValueError, match="Sign in"):
        await tools.save_asset(sefaria, {"html": HTML})


async def test_token_mint_failure_stops_before_social():
    http, seen = social()
    with pytest.raises(ValueError, match="did not sign you in"):
        await tools.save_asset(
            FakeSefaria(ValueError("Sefaria did not sign you in to Sefaria Social (HTTP 403).")),
            {"html": HTML},
            http=http,
        )
    assert seen == []


async def test_unreachable_social():
    def handler(request):
        raise httpx.ConnectError("down")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(
        ValueError, match="could not be reached \\(ConnectError\\). Nothing was saved"
    ):
        await tools.save_asset(FakeSefaria(), {"html": HTML}, http=http)


async def test_neither_token_nor_body_is_logged(caplog):
    caplog.set_level(logging.DEBUG)
    http, _ = social()
    await tools.save_asset(FakeSefaria(), {"html": HTML}, http=http)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "social_asset_post" in logged
    assert TOKEN not in logged and "In the beginning" not in logged


async def test_reader_link_without_a_first_ref():
    assert tools.reader_link(None, "a1") is None
    assert (
        tools.reader_link("Rashi on Genesis 1:1:1", "a 1")
        == "https://www.sefaria.test/Rashi_on_Genesis.1.1.1?a2=a%201&link=2-1"
    )
    assert (
        tools.reader_link("Genesis 1:1", "a1", "v9")
        == "https://www.sefaria.test/Genesis.1.1?a2=a1@v9&link=2-1"
    )
