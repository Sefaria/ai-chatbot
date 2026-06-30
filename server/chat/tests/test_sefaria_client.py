"""Tests for SefariaClient - API calls and parameter handling."""

import json
import os
from unittest.mock import AsyncMock, Mock, patch

import pytest

from chat.V2.agent.sefaria_client import DEFAULT_SEFARIA_BASE_URL, SefariaClient


@pytest.fixture
def client():
    """Create a SefariaClient instance."""
    return SefariaClient(base_url="https://www.sefaria.org")


@pytest.fixture
def mock_http_response():
    """Create a mock HTTP response."""
    mock_response = Mock()
    mock_response.json = Mock(return_value={})
    mock_response.raise_for_status = Mock()
    return mock_response


@pytest.fixture
def mock_http_client(mock_http_response):
    """Create a mock HTTP client."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_http_response)
    mock_client.post = AsyncMock(return_value=mock_http_response)
    return mock_client


class TestSefariaClientInit:
    """Test SefariaClient initialization."""

    def test_default_base_url(self):
        with patch.dict(
            os.environ,
            {
                "SEFARIA_API_BASE_URL": "",
            },
        ):
            client = SefariaClient()
        assert client.base_url == "https://www.sefaria.org"
        assert client.base_url == DEFAULT_SEFARIA_BASE_URL

    def test_env_base_url(self):
        with patch.dict(
            os.environ,
            {
                "SEFARIA_API_BASE_URL": "https://www.personalization.cauldron.sefaria.org",
            },
        ):
            client = SefariaClient()

        assert client.base_url == "https://www.personalization.cauldron.sefaria.org"

    def test_custom_base_url(self):
        client = SefariaClient(base_url="https://custom.sefaria.org/")
        assert client.base_url == "https://custom.sefaria.org"  # trailing slash stripped

    def test_default_timeout(self):
        client = SefariaClient()
        assert client.timeout == 30.0

    def test_custom_timeout(self):
        client = SefariaClient(timeout=60.0)
        assert client.timeout == 60.0


class TestGetTextVersionLanguage:
    """Test get_text parameter handling."""

    @pytest.mark.asyncio
    async def test_omits_version_param(self, client, mock_http_client):
        """get_text should not include a version param in the URL."""
        mock_http_client.get.return_value.json = AsyncMock(
            return_value={"versions": [], "available_versions": []}
        )

        with patch.object(client, "_get_client", return_value=mock_http_client):
            await client.get_text("Genesis 1:1")

            call_args = mock_http_client.get.call_args
            url = call_args[0][0]

            assert "version=" not in url


class TestGetTextReferenceEncoding:
    """Test reference encoding in get_text."""

    @pytest.mark.asyncio
    async def test_encodes_space_in_reference(self, client, mock_http_client):
        """References with spaces should be URL encoded."""
        mock_http_client.get.return_value.json = AsyncMock(
            return_value={"versions": [], "available_versions": []}
        )

        with patch.object(client, "_get_client", return_value=mock_http_client):
            await client.get_text("Genesis 1:1")

            call_args = mock_http_client.get.call_args
            url = call_args[0][0]

            # Space should be encoded as %20
            assert "Genesis%201%3A1" in url or "Genesis+1" in url

    @pytest.mark.asyncio
    async def test_encodes_hebrew_reference(self, client, mock_http_client):
        """Hebrew references should be URL encoded."""
        mock_http_client.get.return_value.json = AsyncMock(
            return_value={"versions": [], "available_versions": []}
        )

        with patch.object(client, "_get_client", return_value=mock_http_client):
            await client.get_text("בראשית א:א")

            call_args = mock_http_client.get.call_args
            url = call_args[0][0]

            # Hebrew characters should be percent-encoded
            assert "api/v3/texts/" in url
            assert "בראשית" not in url  # Should be encoded


class TestGetAuthorIndexes:
    """Test get_author_indexes endpoint and query parameters."""

    @pytest.mark.asyncio
    async def test_author_indexes_without_optional_flags(self, client, mock_http_client):
        mock_http_client.get.return_value.json = AsyncMock(
            return_value={"author": {"slug": "rambam"}, "indexes": []}
        )

        with patch.object(client, "_get_client", return_value=mock_http_client):
            await client.get_author_indexes("rambam")

            call_args = mock_http_client.get.call_args
            url = call_args[0][0]

            assert "api/authors/rambam/indexes" in url
            assert "include_aggregations" not in url
            assert "include_descriptions" not in url

    @pytest.mark.asyncio
    async def test_author_indexes_with_optional_flags(self, client, mock_http_client):
        mock_http_client.get.return_value.json = AsyncMock(
            return_value={"author": {"slug": "rambam"}, "indexes": []}
        )

        with patch.object(client, "_get_client", return_value=mock_http_client):
            await client.get_author_indexes(
                "rambam", include_aggregations=True, include_descriptions=True
            )

            call_args = mock_http_client.get.call_args
            url = call_args[0][0]

            assert "api/authors/rambam/indexes" in url
            assert "include_aggregations=1" in url
            assert "include_descriptions=1" in url

    @pytest.mark.asyncio
    async def test_author_indexes_returns_helpful_message_on_404(self, client):
        """When author slug is missing, should return a recoverable error payload."""
        import httpx

        mock_request = httpx.Request("GET", "https://www.sefaria.org/api/authors/missing/indexes")
        mock_response = httpx.Response(404, request=mock_request)

        with patch.object(
            client,
            "_get_json",
            side_effect=httpx.HTTPStatusError(
                "Not Found", request=mock_request, response=mock_response
            ),
        ):
            result = await client.get_author_indexes("missing")

        assert "not found" in result.get("error", "").lower()
        assert "clarify_name_argument" in result.get("suggestion", "")


class TestClarifySearchPathFilter:
    """Test clarify_search_path_filter parsing."""

    @pytest.mark.asyncio
    async def test_parses_json_string_response_without_embedded_quotes(self, client):
        class MockResponse:
            status_code = 200

            def json(self):
                return "Chasidut/Breslov/Likutei Moharan"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=MockResponse())

        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.clarify_search_path_filter("Likutei Moharan")

        assert result == "Chasidut/Breslov/Likutei Moharan"


class TestOptimizeTextResponse:
    """Test _optimize_text_response method."""

    def test_keeps_essential_fields(self, client):
        data = {
            "ref": "Genesis 1:1",
            "versions": [{"text": "In the beginning", "versionTitle": "JPS"}],
            "available_versions": [{"versionTitle": "JPS"}],
            "extra_field": "should be removed",
            "another_extra": 123,
        }

        result = client._optimize_text_response(data)

        assert "ref" in result
        assert "versions" in result
        assert "available_versions" not in result
        assert "extra_field" not in result
        assert "another_extra" not in result

    def test_handles_non_dict_input(self, client):
        result = client._optimize_text_response("not a dict")
        assert result == "not a dict"

    def test_optimizes_versions_array(self, client):
        data = {
            "versions": [
                {
                    "text": "In the beginning",
                    "versionTitle": "JPS",
                    "languageFamilyName": "english",
                    "versionSource": "http://example.com",
                    "extra": "removed",
                }
            ]
        }

        result = client._optimize_text_response(data)

        assert len(result["versions"]) == 1
        version = result["versions"][0]
        assert version["text"] == "In the beginning"
        assert version["versionTitle"] == "JPS"
        assert "extra" not in version


class TestOptimizeLinksResponse:
    """Test _optimize_links_response method."""

    def test_keeps_essential_link_fields(self, client):
        data = [
            {
                "ref": "Rashi on Genesis 1:1",
                "sourceRef": "Genesis 1:1",
                "anchorText": "In the beginning",
                "type": "commentary",
                "category": "Commentary",
                "extra": "removed",
            }
        ]

        result = client._optimize_links_response(data)

        assert len(result) == 1
        link = result[0]
        assert link["ref"] == "Rashi on Genesis 1:1"
        assert "extra" not in link

    def test_truncates_long_text(self, client):
        long_text = "x" * 600
        data = [{"ref": "test", "text": long_text}]

        result = client._optimize_links_response(data)

        assert len(result[0]["text"]) <= 503  # 500 + "..."

    def test_handles_non_list_input(self, client):
        result = client._optimize_links_response({"not": "a list"})
        assert result == {"not": "a list"}


class TestSemanticSearch:
    """Test semantic_search error handling."""

    @pytest.mark.asyncio
    async def test_returns_unavailable_message_on_404(self, client):
        """When endpoint returns 404, should return a helpful message, not raise."""
        import httpx

        # Create a proper mock request and response for httpx error
        mock_request = httpx.Request("POST", "https://www.sefaria.org/api/knn-search")
        mock_response = httpx.Response(404, request=mock_request)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Not Found", request=mock_request, response=mock_response
            )
        )

        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.semantic_search("test query")

        assert "unavailable" in result.get("error", "").lower()
        assert "text_search" in result.get("suggestion", "").lower()

    @pytest.mark.asyncio
    async def test_returns_results_on_success(self, client):
        """When endpoint succeeds, should return the results."""
        expected_results = {"results": [{"ref": "Genesis 1:1"}]}

        # Create a simple mock response with sync methods (not coroutines)
        class MockResponse:
            def raise_for_status(self):
                pass  # No error

            def json(self):
                return expected_results

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MockResponse())

        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.semantic_search("test query")

        assert result == expected_results


class TestTextSearch:
    """Test text_search empty result handling."""

    @pytest.mark.asyncio
    async def test_returns_helpful_message_when_no_results(self, client):
        """When search returns no results, should return a helpful message."""

        # Create mock that returns empty hits
        class MockResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"hits": {"hits": [], "total": {"value": 0}}}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MockResponse())

        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.text_search("nonexistent query xyz123")

        # Should return a dict with no_results indicator and suggestion
        assert isinstance(result, dict)
        assert result.get("no_results") is True
        assert "suggestion" in result

    @pytest.mark.asyncio
    async def test_filtered_search_does_not_fallback_to_unfiltered_results(self, client):
        """Filtered searches should stay scoped and return no_results when empty."""

        class MockResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"hits": {"hits": [], "total": {"value": 0}}}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MockResponse())

        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.text_search("beginning", ["Tanakh/Torah/Genesis"])

        assert isinstance(result, dict)
        assert result.get("no_results") is True
        assert "within the requested book or filter scope" in result["suggestion"]
        assert "Tanakh/Torah/Genesis" in result["suggestion"]
        mock_client.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_results_when_found(self, client):
        """When search finds results, should return the results list."""

        class MockResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "hits": {
                        "hits": [
                            {
                                "_source": {"ref": "Genesis 1:1", "categories": ["Torah"]},
                                "highlight": {"exact": ["In the beginning"]},
                            }
                        ],
                        "total": {"value": 1},
                    }
                }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MockResponse())

        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.text_search("beginning")

        # Should return a list of results
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["ref"] == "Genesis 1:1"


class TestSearchUserSourceSheets:
    """Test authenticated source sheet search."""

    @pytest.mark.asyncio
    async def test_requires_authenticated_user_session(self, client):
        with pytest.raises(ValueError, match="requires authenticated user context"):
            await client.search_user_source_sheets("shabbat")

    @pytest.mark.asyncio
    async def test_calls_sheet_api_with_x_session_id(self, client):
        response = Mock()
        response.json.return_value = {
            "sheets": [
                {
                    "id": 702510,
                    "title": "Sabbath prohibitions",
                    "summary": "Theory about melacha",
                    "sheetUrl": "/sheets/702510",
                    "tags": ["Shabbat"],
                    "topics": [{"asTyped": "שבת", "slug": "shabbat", "he": "שבת", "en": "Shabbat"}],
                }
            ]
        }
        response.raise_for_status = Mock()
        mock_client = Mock()
        mock_client.get = AsyncMock(return_value=response)

        client.set_user_session("186013", "encrypted-token")

        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.search_user_source_sheets("prohibitions", limit=5)

        mock_client.get.assert_called_once()
        call_args = mock_client.get.call_args
        assert call_args.args[0] == f"{client.base_url}/api/sheets/user/186013/date/0/0"
        assert call_args.kwargs["headers"]["X-Session-ID"] == "encrypted-token"
        assert result["total_matches"] == 1
        assert result["sheets"][0]["matched_fields"] == ["title"]

    @pytest.mark.asyncio
    async def test_filters_across_title_summary_tags_and_topics(self, client):
        client.set_user_session("186013", "encrypted-token")
        sheets = [
            {
                "id": 1,
                "title": "Hilchot Shabbat",
                "summary": "A structured learning workflow",
                "sheetUrl": "/sheets/1",
                "tags": ["halacha"],
                "topics": [],
            },
            {
                "id": 2,
                "title": "Pesach Notes",
                "summary": "Festival prep",
                "sheetUrl": "/sheets/2",
                "tags": [],
                "topics": [{"asTyped": "Shabbat", "slug": "shabbat", "he": "שבת", "en": "Shabbat"}],
            },
        ]

        with patch.object(client, "_get_json", AsyncMock(return_value={"sheets": sheets})):
            result = await client.search_user_source_sheets("shabbat workflow", limit=10)

        assert result["total_matches"] == 2
        assert result["sheets"][0]["id"] == 1
        assert set(result["sheets"][0]["matched_fields"]) == {"title", "summary"}
        assert result["sheets"][1]["id"] == 2


class TestGetSourceSheet:
    """Test loading and normalizing a source sheet."""

    @pytest.mark.asyncio
    async def test_calls_sheet_endpoint_with_retained_session_token(self, client):
        response = Mock()
        response.json.return_value = {
            "_id": "696c836aad417adc76f0f9e6",
            "id": 702510,
            "status": "unlisted",
            "title": "מבחן",
            "sources": [
                {"outsideText": "<p>שלום וברכה</p>", "node": 2},
                {
                    "ref": "Genesis 3:1",
                    "heRef": "בראשית ג׳:א׳",
                    "text": {
                        "en": "<p><small>(1)</small> Now the serpent was here.</p>",
                        "he": "<p><small>(א)</small> וְהַנָּחָשׁ הָיָה עָרוּם.</p>",
                    },
                    "node": 10,
                },
            ],
        }
        response.raise_for_status = Mock()
        mock_client = Mock()
        mock_client.get = AsyncMock(return_value=response)

        client.set_user_session("186013", "encrypted-token")

        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.get_source_sheet(702510)

        mock_client.get.assert_called_once()
        call_args = mock_client.get.call_args
        assert call_args.args[0] == f"{client.base_url}/api/sheets/702510"
        assert call_args.kwargs["headers"]["X-Session-ID"] == "encrypted-token"
        assert result["title"] == "מבחן"
        assert result["source_count"] == 2
        assert result["sources"][0]["outsideText"] == "שלום וברכה"
        assert result["sources"][1]["ref"] == "Genesis 3:1"
        assert "Now the serpent was here." in result["sources"][1]["text"]["en"]

    @pytest.mark.asyncio
    async def test_works_without_retained_session_token(self, client):
        with patch.object(
            client,
            "_get_json",
            AsyncMock(return_value={"title": "Public Sheet", "status": "public", "sources": []}),
        ) as mock_get_json:
            result = await client.get_source_sheet(123)

        mock_get_json.assert_awaited_once_with(
            "api/sheets/123",
            headers={"Accept": "application/json"},
        )
        assert result["title"] == "Public Sheet"
        assert result["source_count"] == 0


class TestCreateSourceSheet:
    """Test creating authenticated source sheets."""

    @pytest.mark.asyncio
    async def test_requires_authenticated_user_session(self, client):
        with pytest.raises(ValueError, match="requires authenticated user context"):
            await client.create_source_sheet(
                "Bereshit",
                "A starter sheet",
                [{"outsideText": "<p>hi there</p>", "node": 1}],
            )

    @pytest.mark.asyncio
    async def test_hydrates_ref_sources_and_posts_form_payload(self, client):
        response = Mock()
        response.json.return_value = {
            "id": 715437,
            "status": "unlisted",
            "title": "Bereshit",
            "summary": "A starter sheet",
            "sources": [
                {"outsideText": "<p>hi there</p>", "node": 1},
                {"outsideText": "<p>this is text</p>", "node": 2},
                {
                    "ref": "Genesis 3:1",
                    "heRef": "בראשית ג׳:א׳",
                    "text": {
                        "en": "<p>Now the serpent was the shrewdest.</p>",
                        "he": "<p>וְהַנָּחָשׁ הָיָה עָרוּם.</p>",
                    },
                    "node": 3,
                },
            ],
        }
        response.raise_for_status = Mock()
        mock_client = Mock()
        mock_client.post = AsyncMock(return_value=response)

        client.set_user_session("186013", "encrypted-token")

        with patch.object(client, "_get_client", return_value=mock_client):
            with patch.object(
                client,
                "get_text",
                AsyncMock(
                    return_value={
                        "versions": [
                            {
                                "languageFamilyName": "english",
                                "text": ["Now the serpent was the shrewdest."],
                            },
                            {
                                "languageFamilyName": "hebrew",
                                "text": ["וְהַנָּחָשׁ הָיָה עָרוּם."],
                            },
                        ]
                    }
                ),
            ) as mock_get_text:
                result = await client.create_source_sheet(
                    "Bereshit",
                    "A starter sheet",
                    [
                        {"outsideText": "<p>hi there</p>", "node": 1},
                        {"outsideText": "<p>this is text</p>"},
                        {"ref": "Genesis 3:1", "heRef": "בראשית ג׳:א׳"},
                    ],
                )

        mock_get_text.assert_awaited_once_with("Genesis 3:1")
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args.args[0] == f"{client.base_url}/api/sheets/"
        assert call_args.kwargs["headers"]["X-Session-ID"] == "encrypted-token"

        posted_payload = json.loads(call_args.kwargs["data"]["json"])
        assert posted_payload["title"] == "Bereshit"
        assert posted_payload["summary"] == "A starter sheet"
        assert posted_payload["status"] == "unlisted"
        assert posted_payload["options"]["language"] == "bilingual"
        assert posted_payload["sources"][1]["node"] == 2
        assert posted_payload["sources"][2]["node"] == 3
        assert (
            posted_payload["sources"][2]["text"]["en"]
            == "<p>Now the serpent was the shrewdest.</p>"
        )
        assert posted_payload["sources"][2]["text"]["he"] == "<p>וְהַנָּחָשׁ הָיָה עָרוּם.</p>"
        assert posted_payload["nextNode"] == 4

        assert result["id"] == 715437
        assert result["sheetUrl"] == f"{client.base_url}/sheets/715437"
        assert result["source_count"] == 3
        assert result["sources"][2]["ref"] == "Genesis 3:1"


class TestSearchInBook:
    """Test search_in_book scoped path resolution."""

    @pytest.mark.asyncio
    async def test_search_in_book_uses_unquoted_filter_path(self, client):
        with (
            patch.object(
                client,
                "clarify_search_path_filter",
                AsyncMock(return_value="Chasidut/Breslov/Likutei Moharan"),
            ),
            patch.object(
                client,
                "text_search",
                AsyncMock(return_value={"results": []}),
            ) as mock_text_search,
        ):
            await client.search_in_book("פרעה", "Likutei Moharan", 10)

        mock_text_search.assert_awaited_once_with("פרעה", ["Chasidut/Breslov/Likutei Moharan"], 10)
