"""
Tests for SefariaToolExecutor - tool dispatch, error handling, result wrapping.
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch

from chat.agent.tool_executor import (
    SefariaToolExecutor,
    ToolResult,
    describe_tool_call,
)


class TestToolResult:
    """Test ToolResult dataclass."""

    def test_successful_result(self):
        """Test successful result creation."""
        result = ToolResult(
            content=[{"type": "text", "text": "Some result"}],
            is_error=False,
        )
        assert result.is_error is False
        assert len(result.content) == 1
        assert result.content[0]["text"] == "Some result"

    def test_error_result(self):
        """Test error result creation."""
        result = ToolResult(
            content=[{"type": "text", "text": '{"error": "Something went wrong"}'}],
            is_error=True,
        )
        assert result.is_error is True


class TestSefariaToolExecutorInit:
    """Test SefariaToolExecutor initialization."""

    def test_init_with_default_client(self):
        """Test initialization with default client."""
        executor = SefariaToolExecutor()
        assert executor.client is not None

    def test_init_with_custom_client(self):
        """Test initialization with custom client."""
        mock_client = Mock()
        executor = SefariaToolExecutor(client=mock_client)
        assert executor.client == mock_client


class TestToolDispatch:
    """Test tool dispatch to correct methods."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock Sefaria client."""
        client = Mock()
        # Make all methods async
        client.get_text = AsyncMock(return_value={"text": "In the beginning..."})
        client.text_search = AsyncMock(return_value={"results": []})
        client.get_current_calendar = AsyncMock(return_value={"date": "2024-01-15"})
        client.english_semantic_search = AsyncMock(return_value={"results": []})
        client.get_links_between_texts = AsyncMock(return_value={"links": []})
        client.search_in_book = AsyncMock(return_value={"results": []})
        client.search_in_dictionaries = AsyncMock(return_value={"entries": []})
        client.get_english_translations = AsyncMock(return_value={"translations": []})
        client.get_topic_details = AsyncMock(return_value={"topic": "shabbat"})
        client.clarify_name_argument = AsyncMock(return_value={"suggestions": []})
        client.clarify_search_path_filter = AsyncMock(return_value="Tanakh/Torah/Genesis")
        client.get_text_or_category_shape = AsyncMock(return_value={"shape": []})
        client.get_text_catalogue_info = AsyncMock(return_value={"info": {}})
        client.get_available_manuscripts = AsyncMock(return_value={"manuscripts": []})
        client.get_manuscript_image = AsyncMock(return_value={"image_url": "http://..."})
        return client

    @pytest.fixture
    def executor(self, mock_client):
        """Create executor with mock client."""
        return SefariaToolExecutor(client=mock_client)

    @pytest.mark.asyncio
    async def test_dispatch_get_text(self, executor, mock_client):
        """Test get_text dispatch."""
        result = await executor.execute(
            "get_text",
            {"reference": "Genesis 1:1", "version_language": "en"},
        )
        mock_client.get_text.assert_called_once_with("Genesis 1:1", "en")
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_dispatch_get_text_without_version(self, executor, mock_client):
        """Test get_text without version language."""
        await executor.execute("get_text", {"reference": "Genesis 1:1"})
        mock_client.get_text.assert_called_once_with("Genesis 1:1", None)

    @pytest.mark.asyncio
    async def test_dispatch_text_search(self, executor, mock_client):
        """Test text_search dispatch."""
        await executor.execute(
            "text_search",
            {"query": "shabbat", "filters": "Talmud", "size": 20},
        )
        mock_client.text_search.assert_called_once_with("shabbat", "Talmud", 20)

    @pytest.mark.asyncio
    async def test_dispatch_text_search_defaults(self, executor, mock_client):
        """Test text_search with default values."""
        await executor.execute("text_search", {"query": "prayer"})
        mock_client.text_search.assert_called_once_with("prayer", None, 10)

    @pytest.mark.asyncio
    async def test_dispatch_get_current_calendar(self, executor, mock_client):
        """Test get_current_calendar dispatch."""
        await executor.execute("get_current_calendar", {})
        mock_client.get_current_calendar.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_english_semantic_search(self, executor, mock_client):
        """Test english_semantic_search dispatch."""
        await executor.execute(
            "english_semantic_search",
            {"query": "meaning of life", "filters": "Philosophy"},
        )
        mock_client.english_semantic_search.assert_called_once_with(
            "meaning of life", "Philosophy"
        )

    @pytest.mark.asyncio
    async def test_dispatch_get_links_between_texts(self, executor, mock_client):
        """Test get_links_between_texts dispatch."""
        await executor.execute(
            "get_links_between_texts",
            {"reference": "Genesis 1:1", "with_text": "1"},
        )
        mock_client.get_links_between_texts.assert_called_once_with("Genesis 1:1", "1")

    @pytest.mark.asyncio
    async def test_dispatch_search_in_book(self, executor, mock_client):
        """Test search_in_book dispatch."""
        await executor.execute(
            "search_in_book",
            {"query": "light", "book_name": "Genesis", "size": 5},
        )
        mock_client.search_in_book.assert_called_once_with("light", "Genesis", 5)

    @pytest.mark.asyncio
    async def test_dispatch_search_in_dictionaries(self, executor, mock_client):
        """Test search_in_dictionaries dispatch."""
        await executor.execute(
            "search_in_dictionaries",
            {"query": "torah"},
        )
        mock_client.search_in_dictionaries.assert_called_once_with("torah")

    @pytest.mark.asyncio
    async def test_dispatch_get_english_translations(self, executor, mock_client):
        """Test get_english_translations dispatch."""
        await executor.execute(
            "get_english_translations",
            {"reference": "Psalm 23:1"},
        )
        mock_client.get_english_translations.assert_called_once_with("Psalm 23:1")

    @pytest.mark.asyncio
    async def test_dispatch_get_topic_details(self, executor, mock_client):
        """Test get_topic_details dispatch."""
        await executor.execute(
            "get_topic_details",
            {"topic_slug": "shabbat", "with_links": True, "with_refs": True},
        )
        mock_client.get_topic_details.assert_called_once_with("shabbat", True, True)

    @pytest.mark.asyncio
    async def test_dispatch_clarify_name_argument(self, executor, mock_client):
        """Test clarify_name_argument dispatch."""
        await executor.execute(
            "clarify_name_argument",
            {"name": "rashi", "limit": 5, "type_filter": "Person"},
        )
        mock_client.clarify_name_argument.assert_called_once_with("rashi", 5, "Person")

    @pytest.mark.asyncio
    async def test_dispatch_clarify_search_path_filter(self, executor, mock_client):
        """Test clarify_search_path_filter dispatch."""
        result = await executor.execute(
            "clarify_search_path_filter",
            {"book_name": "Genesis"},
        )
        mock_client.clarify_search_path_filter.assert_called_once_with("Genesis")
        # Should wrap the filter path in a dict
        assert "filter_path" in result.content[0]["text"]

    @pytest.mark.asyncio
    async def test_dispatch_get_text_or_category_shape(self, executor, mock_client):
        """Test get_text_or_category_shape dispatch."""
        await executor.execute(
            "get_text_or_category_shape",
            {"name": "Genesis"},
        )
        mock_client.get_text_or_category_shape.assert_called_once_with("Genesis")

    @pytest.mark.asyncio
    async def test_dispatch_get_text_catalogue_info(self, executor, mock_client):
        """Test get_text_catalogue_info dispatch."""
        await executor.execute(
            "get_text_catalogue_info",
            {"title": "Mishnah Berakhot"},
        )
        mock_client.get_text_catalogue_info.assert_called_once_with("Mishnah Berakhot")

    @pytest.mark.asyncio
    async def test_dispatch_get_available_manuscripts(self, executor, mock_client):
        """Test get_available_manuscripts dispatch."""
        await executor.execute(
            "get_available_manuscripts",
            {"reference": "Genesis 1:1"},
        )
        mock_client.get_available_manuscripts.assert_called_once_with("Genesis 1:1")

    @pytest.mark.asyncio
    async def test_dispatch_get_manuscript_image(self, executor, mock_client):
        """Test get_manuscript_image dispatch."""
        await executor.execute(
            "get_manuscript_image",
            {"image_url": "http://example.com/image.jpg", "manuscript_title": "Leningrad Codex"},
        )
        mock_client.get_manuscript_image.assert_called_once_with(
            "http://example.com/image.jpg", "Leningrad Codex"
        )


class TestErrorHandling:
    """Test error handling in tool execution."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock client that raises errors."""
        client = Mock()
        client.get_text = AsyncMock(side_effect=Exception("API Error"))
        return client

    @pytest.fixture
    def executor(self, mock_client):
        return SefariaToolExecutor(client=mock_client)

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        """Test handling of unknown tool name."""
        executor = SefariaToolExecutor(client=Mock())
        result = await executor.execute("unknown_tool", {})
        assert result.is_error is True
        assert "Unknown tool" in result.content[0]["text"]

    @pytest.mark.asyncio
    async def test_client_exception(self, executor):
        """Test handling of client exceptions."""
        result = await executor.execute("get_text", {"reference": "Genesis 1:1"})
        assert result.is_error is True
        assert "API Error" in result.content[0]["text"]


class TestResultWrapping:
    """Test result wrapping methods."""

    def test_wrap_string_result(self):
        """Test wrapping a string result."""
        executor = SefariaToolExecutor(client=Mock())
        result = executor._wrap("Simple text result")
        assert result.is_error is False
        assert result.content[0]["type"] == "text"
        assert result.content[0]["text"] == "Simple text result"

    def test_wrap_dict_result(self):
        """Test wrapping a dict result."""
        executor = SefariaToolExecutor(client=Mock())
        result = executor._wrap({"key": "value", "number": 123})
        assert result.is_error is False
        assert '"key": "value"' in result.content[0]["text"]

    def test_wrap_list_result(self):
        """Test wrapping a list result."""
        executor = SefariaToolExecutor(client=Mock())
        result = executor._wrap([1, 2, 3])
        assert result.is_error is False
        # JSON formatting may include newlines, check for list content
        assert "1" in result.content[0]["text"]
        assert "2" in result.content[0]["text"]
        assert "3" in result.content[0]["text"]

    def test_wrap_unicode_result(self):
        """Test wrapping result with Unicode."""
        executor = SefariaToolExecutor(client=Mock())
        result = executor._wrap({"hebrew": "בראשית"})
        assert result.is_error is False
        assert "בראשית" in result.content[0]["text"]

    def test_wrap_error(self):
        """Test wrapping an error."""
        executor = SefariaToolExecutor(client=Mock())
        result = executor._wrap_error("Something went wrong")
        assert result.is_error is True
        assert "error" in result.content[0]["text"]
        assert "Something went wrong" in result.content[0]["text"]


class TestDescribeToolCall:
    """Test describe_tool_call function."""

    def test_describe_text_search(self):
        """Test description for text_search."""
        desc = describe_tool_call("text_search", {"query": "shabbat"})
        assert "Searching texts" in desc
        assert "shabbat" in desc

    def test_describe_text_search_with_filters(self):
        """Test description for text_search with filters."""
        desc = describe_tool_call(
            "text_search",
            {"query": "prayer", "filters": "Talmud"},
        )
        assert "prayer" in desc
        assert "Talmud" in desc

    def test_describe_get_text(self):
        """Test description for get_text."""
        desc = describe_tool_call("get_text", {"reference": "Genesis 1:1"})
        assert "Fetching text" in desc
        assert "Genesis 1:1" in desc

    def test_describe_get_text_with_version(self):
        """Test description for get_text with version."""
        desc = describe_tool_call(
            "get_text",
            {"reference": "Genesis 1:1", "version_language": "he"},
        )
        assert "Genesis 1:1" in desc
        assert "he" in desc

    def test_describe_english_semantic_search(self):
        """Test description for english_semantic_search."""
        desc = describe_tool_call(
            "english_semantic_search",
            {"query": "meaning of life"},
        )
        assert "Semantic search" in desc
        assert "meaning of life" in desc

    def test_describe_search_in_book(self):
        """Test description for search_in_book."""
        desc = describe_tool_call(
            "search_in_book",
            {"query": "light", "book_name": "Genesis"},
        )
        assert "Searching in" in desc
        assert "Genesis" in desc
        assert "light" in desc

    def test_describe_get_links_between_texts(self):
        """Test description for get_links_between_texts."""
        desc = describe_tool_call(
            "get_links_between_texts",
            {"reference": "Exodus 20:1"},
        )
        assert "Finding links" in desc
        assert "Exodus 20:1" in desc

    def test_describe_get_topic_details(self):
        """Test description for get_topic_details."""
        desc = describe_tool_call(
            "get_topic_details",
            {"topic_slug": "shabbat"},
        )
        assert "topic details" in desc
        assert "shabbat" in desc

    def test_describe_get_current_calendar(self):
        """Test description for get_current_calendar."""
        desc = describe_tool_call("get_current_calendar", {})
        assert "calendar" in desc

    def test_describe_clarify_name_argument(self):
        """Test description for clarify_name_argument."""
        desc = describe_tool_call(
            "clarify_name_argument",
            {"name": "rashi"},
        )
        assert "Clarifying name" in desc
        assert "rashi" in desc

    def test_describe_unknown_tool(self):
        """Test description for unknown tool."""
        desc = describe_tool_call("unknown_tool", {"arg": "value"})
        assert "Running tool" in desc
        assert "unknown_tool" in desc

    def test_describe_truncates_long_values(self):
        """Test that long values are truncated."""
        long_query = "x" * 200
        desc = describe_tool_call("text_search", {"query": long_query})
        assert "…" in desc  # Should have ellipsis indicating truncation
        assert len(desc) < len(long_query) + 50  # Should be shorter
