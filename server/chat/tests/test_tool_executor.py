"""Tests for SefariaToolExecutor - tool dispatch, error handling, result wrapping."""

from unittest.mock import AsyncMock, Mock

import pytest

from chat.agent.tool_executor import (
    SefariaToolExecutor,
    ToolResult,
    describe_tool_call,
)


class TestToolResult:
    """Test ToolResult dataclass."""

    def test_successful_result(self):
        result = ToolResult(
            content=[{"type": "text", "text": "Some result"}],
            is_error=False,
        )
        assert result.is_error is False
        assert result.content[0]["text"] == "Some result"

    def test_error_result(self):
        result = ToolResult(
            content=[{"type": "text", "text": '{"error": "Something went wrong"}'}],
            is_error=True,
        )
        assert result.is_error is True


class TestSefariaToolExecutorInit:
    """Test SefariaToolExecutor initialization."""

    def test_init_with_default_client(self):
        executor = SefariaToolExecutor()
        assert executor.client is not None

    def test_init_with_custom_client(self):
        mock_client = Mock()
        executor = SefariaToolExecutor(client=mock_client)
        assert executor.client == mock_client


@pytest.fixture
def mock_client():
    """Create a mock Sefaria client with all async methods."""
    client = Mock()
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
def executor(mock_client):
    """Create executor with mock client."""
    return SefariaToolExecutor(client=mock_client)


class TestToolDispatch:
    """Test tool dispatch to correct methods."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tool_name,args,expected_method,expected_args",
        [
            (
                "get_text",
                {"reference": "Genesis 1:1", "version_language": "en"},
                "get_text",
                ("Genesis 1:1", "en"),
            ),
            ("get_text", {"reference": "Genesis 1:1"}, "get_text", ("Genesis 1:1", None)),
            (
                "text_search",
                {"query": "shabbat", "filters": "Talmud", "size": 20},
                "text_search",
                ("shabbat", "Talmud", 20),
            ),
            ("text_search", {"query": "prayer"}, "text_search", ("prayer", None, 10)),
            ("get_current_calendar", {}, "get_current_calendar", ()),
            (
                "english_semantic_search",
                {"query": "meaning of life", "filters": "Philosophy"},
                "english_semantic_search",
                ("meaning of life", "Philosophy"),
            ),
            (
                "get_links_between_texts",
                {"reference": "Genesis 1:1", "with_text": "1"},
                "get_links_between_texts",
                ("Genesis 1:1", "1"),
            ),
            (
                "search_in_book",
                {"query": "light", "book_name": "Genesis", "size": 5},
                "search_in_book",
                ("light", "Genesis", 5),
            ),
            ("search_in_dictionaries", {"query": "torah"}, "search_in_dictionaries", ("torah",)),
            (
                "get_english_translations",
                {"reference": "Psalm 23:1"},
                "get_english_translations",
                ("Psalm 23:1",),
            ),
            (
                "get_topic_details",
                {"topic_slug": "shabbat", "with_links": True, "with_refs": True},
                "get_topic_details",
                ("shabbat", True, True),
            ),
            (
                "clarify_name_argument",
                {"name": "rashi", "limit": 5, "type_filter": "Person"},
                "clarify_name_argument",
                ("rashi", 5, "Person"),
            ),
            (
                "get_text_or_category_shape",
                {"name": "Genesis"},
                "get_text_or_category_shape",
                ("Genesis",),
            ),
            (
                "get_text_catalogue_info",
                {"title": "Mishnah Berakhot"},
                "get_text_catalogue_info",
                ("Mishnah Berakhot",),
            ),
            (
                "get_available_manuscripts",
                {"reference": "Genesis 1:1"},
                "get_available_manuscripts",
                ("Genesis 1:1",),
            ),
            (
                "get_manuscript_image",
                {
                    "image_url": "http://example.com/image.jpg",
                    "manuscript_title": "Leningrad Codex",
                },
                "get_manuscript_image",
                ("http://example.com/image.jpg", "Leningrad Codex"),
            ),
        ],
    )
    async def test_dispatch(
        self, executor, mock_client, tool_name, args, expected_method, expected_args
    ):
        result = await executor.execute(tool_name, args)
        getattr(mock_client, expected_method).assert_called_once_with(*expected_args)
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_clarify_search_path_filter_wraps_result(self, executor, mock_client):
        result = await executor.execute("clarify_search_path_filter", {"book_name": "Genesis"})
        mock_client.clarify_search_path_filter.assert_called_once_with("Genesis")
        assert "filter_path" in result.content[0]["text"]


class TestErrorHandling:
    """Test error handling in tool execution."""

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        executor = SefariaToolExecutor(client=Mock())
        result = await executor.execute("unknown_tool", {})
        assert result.is_error is True
        assert "Unknown tool" in result.content[0]["text"]

    @pytest.mark.asyncio
    async def test_client_exception(self):
        mock_client = Mock()
        mock_client.get_text = AsyncMock(side_effect=Exception("API Error"))
        executor = SefariaToolExecutor(client=mock_client)

        result = await executor.execute("get_text", {"reference": "Genesis 1:1"})
        assert result.is_error is True
        assert "API Error" in result.content[0]["text"]


class TestResultWrapping:
    """Test result wrapping methods."""

    @pytest.fixture
    def executor(self):
        return SefariaToolExecutor(client=Mock())

    @pytest.mark.parametrize(
        "input_data,check_func",
        [
            ("Simple text result", lambda r: r.content[0]["text"] == "Simple text result"),
            ({"key": "value", "number": 123}, lambda r: '"key": "value"' in r.content[0]["text"]),
            ([1, 2, 3], lambda r: all(str(n) in r.content[0]["text"] for n in [1, 2, 3])),
            ({"hebrew": "בראשית"}, lambda r: "בראשית" in r.content[0]["text"]),
        ],
    )
    def test_wrap_result(self, executor, input_data, check_func):
        result = executor._wrap(input_data)
        assert result.is_error is False
        assert result.content[0]["type"] == "text"
        assert check_func(result)

    def test_wrap_error(self, executor):
        result = executor._wrap_error("Something went wrong")
        assert result.is_error is True
        assert "error" in result.content[0]["text"]
        assert "Something went wrong" in result.content[0]["text"]


class TestDescribeToolCall:
    """Test describe_tool_call function."""

    @pytest.mark.parametrize(
        "tool_name,args,expected_phrases",
        [
            ("text_search", {"query": "shabbat"}, ["Searching texts", "shabbat"]),
            ("text_search", {"query": "prayer", "filters": "Talmud"}, ["prayer", "Talmud"]),
            ("get_text", {"reference": "Genesis 1:1"}, ["Fetching text", "Genesis 1:1"]),
            (
                "get_text",
                {"reference": "Genesis 1:1", "version_language": "he"},
                ["Genesis 1:1", "he"],
            ),
            (
                "english_semantic_search",
                {"query": "meaning of life"},
                ["Semantic search", "meaning of life"],
            ),
            (
                "search_in_book",
                {"query": "light", "book_name": "Genesis"},
                ["Searching in", "Genesis", "light"],
            ),
            (
                "get_links_between_texts",
                {"reference": "Exodus 20:1"},
                ["Finding links", "Exodus 20:1"],
            ),
            ("get_topic_details", {"topic_slug": "shabbat"}, ["topic details", "shabbat"]),
            ("get_current_calendar", {}, ["calendar"]),
            ("clarify_name_argument", {"name": "rashi"}, ["Clarifying name", "rashi"]),
            ("unknown_tool", {"arg": "value"}, ["Running tool", "unknown_tool"]),
        ],
    )
    def test_describe_tool(self, tool_name, args, expected_phrases):
        desc = describe_tool_call(tool_name, args)
        for phrase in expected_phrases:
            assert phrase in desc

    def test_truncates_long_values(self):
        long_query = "x" * 200
        desc = describe_tool_call("text_search", {"query": long_query})
        assert "..." in desc or "…" in desc
        assert len(desc) < len(long_query) + 50
