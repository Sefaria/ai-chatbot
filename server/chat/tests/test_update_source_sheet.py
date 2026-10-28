"""Updating a user's own source sheet: the merge rules and the read-then-write call."""

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from chat.V2.agent.sefaria_client import SefariaClient
from chat.V2.agent.source_sheet_serializer import merge_source_sheet_sources

MEDIA = {"node": 3, "media": "https://storage.googleapis.com/sheet-user-uploaded-media/a.png"}
EXISTING = [
    {"node": 1, "outsideText": "<p>Intro</p>"},
    {
        "node": 2,
        "ref": "Genesis 1:1",
        "heRef": "בראשית א׳:א׳",
        "text": {"en": "<p>In</p>", "he": "<p>ב</p>"},
    },
    MEDIA,
]


class TestMergeSources:
    def test_keeps_rewrites_reorders_and_adds(self):
        merged, next_node = merge_source_sheet_sources(
            EXISTING,
            [
                {"node": 3},
                {"node": 1, "outsideText": "<p>New intro</p>"},
                {"ref": "Exodus 1:1", "heRef": "שמות א׳:א׳"},
                {"node": 2},
            ],
            next_node=4,
        )
        assert [s["node"] for s in merged] == [3, 1, 4, 2]
        assert merged[0] == MEDIA
        assert merged[1]["outsideText"] == "<p>New intro</p>"
        assert merged[2] == {"ref": "Exodus 1:1", "heRef": "שמות א׳:א׳", "node": 4}
        assert merged[3] == EXISTING[1]
        assert next_node == 5

    def test_removes_text_sources_left_out(self):
        merged, _ = merge_source_sheet_sources(EXISTING, [{"node": 2}, {"node": 3}], next_node=4)
        assert [s["node"] for s in merged] == [2, 3]

    def test_replacing_a_source_with_a_ref_drops_its_old_text(self):
        merged, _ = merge_source_sheet_sources(
            EXISTING, [{"node": 2, "ref": "Exodus 2:1", "heRef": "שמות ב׳:א׳"}, {"node": 3}], 4
        )
        assert merged[0] == {"ref": "Exodus 2:1", "heRef": "שמות ב׳:א׳", "node": 2}

    @pytest.mark.parametrize(
        ("changes", "message"),
        [
            ([{"node": 1}, {"node": 2}], "media sources can't be removed"),
            ([{"node": 9}, {"node": 3}], "no source with node 9"),
            ([{"node": 1}, {"node": 1}, {"node": 3}], "listed twice"),
            ([{"node": 3, "ref": "Exodus 1:1", "heRef": "שמות"}], "can't be replaced"),
            ([{"node": 2, "outsideText": "x"}, {"node": 3}], "isn't an outside-text source"),
            ([], "at least one source"),
        ],
    )
    def test_refuses_unsafe_or_unclear_changes(self, changes, message):
        with pytest.raises(ValueError, match=message):
            merge_source_sheet_sources(EXISTING, changes, next_node=4)


def stored_sheet(**fields):
    return {
        "id": 715437,
        "_id": "abc",
        "owner": 186013,
        "status": "public",
        "title": "Mine",
        "summary": "Notes\n\nCreated with Claude.",
        "dateModified": "2026-10-22T10:00:00",
        "nextNode": 4,
        "sources": EXISTING,
        "options": {"language": "bilingual"},
        **fields,
    }


def post_response(status=200, body=None):
    response = Mock()
    response.status_code = status
    response.is_success = 200 <= status < 300
    response.json.return_value = body if body is not None else {"id": 715437, "sources": []}
    response.raise_for_status = Mock()
    return response


@pytest.fixture
def client():
    c = SefariaClient()
    c.set_user_session("186013", "encrypted-token")
    return c


class TestUpdateSourceSheet:
    async def _update(self, client, sheet, response, **kwargs):
        http = Mock()
        http.post = AsyncMock(return_value=response)
        texts = {
            "versions": [
                {"languageFamilyName": "hebrew", "text": ["ש"]},
                {"languageFamilyName": "english", "text": ["Names"]},
            ]
        }
        get_json = AsyncMock(
            side_effect=lambda endpoint, *a, **k: (
                sheet if endpoint.startswith("api/sheets/") else texts
            )
        )
        with (
            patch.object(client, "_get_client", AsyncMock(return_value=http)),
            patch.object(client, "_get_json", get_json),
        ):
            result = await client.update_source_sheet(715437, **kwargs)
        return result, http, get_json

    @pytest.mark.asyncio
    async def test_sends_the_sheet_back_with_changes_and_last_modified(self, client):
        _, http, get_json = await self._update(
            client,
            stored_sheet(),
            post_response(),
            summary="Better notes",
            sources=[{"node": 1}, {"ref": "Exodus 1:1", "heRef": "שמות א׳:א׳"}, {"node": 3}],
        )
        assert get_json.await_args_list[0].args[0] == "api/sheets/715437"
        posted = json.loads(http.post.call_args.kwargs["data"]["json"])
        assert posted["id"] == 715437 and "_id" not in posted
        assert posted["lastModified"] == "2026-10-22T10:00:00"
        assert posted["status"] == "public"
        assert posted["summary"] == "Better notes\n\nCreated with Claude."
        assert [s["node"] for s in posted["sources"]] == [1, 4, 3]
        assert posted["sources"][1]["text"]["en"] == "<p>Names</p>"
        assert posted["nextNode"] == 5
        assert http.post.call_args.kwargs["headers"]["X-Session-ID"] == "encrypted-token"

    @pytest.mark.asyncio
    async def test_title_only_update_leaves_sources_and_summary(self, client):
        _, http, _ = await self._update(
            client, stored_sheet(summary="Mine, by hand"), post_response(), title="New"
        )
        posted = json.loads(http.post.call_args.kwargs["data"]["json"])
        assert posted["title"] == "New"
        assert posted["summary"] == "Mine, by hand"
        assert posted["sources"] == EXISTING

    @pytest.mark.asyncio
    async def test_refuses_sheets_the_user_does_not_own(self, client):
        with pytest.raises(ValueError, match="only update your own"):
            await self._update(client, stored_sheet(owner=99), post_response(), title="x")

    @pytest.mark.asyncio
    async def test_a_concurrent_edit_is_reported_not_overwritten(self, client):
        response = post_response(body={"error": "Sheet updated.", "rebuild": True})
        with pytest.raises(ValueError, match="changed while it was being updated"):
            await self._update(client, stored_sheet(), response, title="x")

    @pytest.mark.asyncio
    async def test_sefaria_refusals_keep_their_message(self, client):
        response = post_response(403, {"error": "MCP clients can only update sheets you own."})
        with pytest.raises(ValueError, match="only update sheets you own"):
            await self._update(client, stored_sheet(), response, title="x")
