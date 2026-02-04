"""Tests for SefariaClient - API calls and parameter handling."""

from unittest.mock import AsyncMock, patch

import pytest

from chat.V2.agent.sefaria_client import SefariaClient


@pytest.fixture
def client():
    """Create a SefariaClient instance."""
    return SefariaClient()


@pytest.fixture
def mock_http_response():
    """Create a mock HTTP response."""
    mock_response = AsyncMock()
    mock_response.json = AsyncMock(return_value={})
    mock_response.raise_for_status = AsyncMock()
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
        client = SefariaClient()
        assert client.base_url == "https://www.sefaria.org"

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
    """Test get_text version_language parameter handling."""

    @pytest.mark.asyncio
    async def test_version_language_both_uses_multiple_params(self, client, mock_http_client):
        """When version_language='both', should send version=english&version=source."""
        mock_http_client.get.return_value.json = AsyncMock(
            return_value={"versions": [], "available_versions": []}
        )

        with patch.object(client, "_get_client", return_value=mock_http_client):
            await client.get_text("Genesis 1:1", version_language="both")

            call_args = mock_http_client.get.call_args
            url = call_args[0][0]

            # The URL should have version=english&version=source, not version=english|source
            assert "version=english" in url, f"URL should contain 'version=english', got: {url}"
            assert "version=source" in url, f"URL should contain 'version=source', got: {url}"
            assert "english|source" not in url, (
                f"URL should NOT contain 'english|source', got: {url}"
            )

    @pytest.mark.asyncio
    async def test_version_language_english_uses_single_param(self, client, mock_http_client):
        """When version_language='english', should send version=english."""
        mock_http_client.get.return_value.json = AsyncMock(
            return_value={"versions": [], "available_versions": []}
        )

        with patch.object(client, "_get_client", return_value=mock_http_client):
            await client.get_text("Genesis 1:1", version_language="english")

            call_args = mock_http_client.get.call_args
            url = call_args[0][0]

            assert "version=english" in url

    @pytest.mark.asyncio
    async def test_version_language_source_uses_single_param(self, client, mock_http_client):
        """When version_language='source', should send version=source."""
        mock_http_client.get.return_value.json = AsyncMock(
            return_value={"versions": [], "available_versions": []}
        )

        with patch.object(client, "_get_client", return_value=mock_http_client):
            await client.get_text("Genesis 1:1", version_language="source")

            call_args = mock_http_client.get.call_args
            url = call_args[0][0]

            assert "version=source" in url

    @pytest.mark.asyncio
    async def test_version_language_none_omits_param(self, client, mock_http_client):
        """When version_language is None, should not include version param."""
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
        assert "available_versions" in result
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


class TestEnglishSemanticSearch:
    """Test english_semantic_search error handling."""

    @pytest.mark.asyncio
    async def test_returns_unavailable_message_on_404(self, client):
        """When endpoint returns 404, should return a helpful message, not raise."""
        import httpx

        # Create a proper mock request and response for httpx error
        mock_request = httpx.Request("POST", "https://ai.sefaria.org/api/knn-search")
        mock_response = httpx.Response(404, request=mock_request)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Not Found", request=mock_request, response=mock_response
            )
        )

        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.english_semantic_search("test query")

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
            result = await client.english_semantic_search("test query")

        assert result == expected_results
