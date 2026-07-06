"""Tests for SefariaToolExecutor - tool dispatch, error handling, result wrapping."""

from unittest.mock import AsyncMock, Mock

import pytest

from chat.V2.agent.tool_executor import (
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
    client.semantic_search = AsyncMock(return_value={"results": []})
    client.get_links_between_texts = AsyncMock(return_value={"links": []})
    client.search_in_book = AsyncMock(return_value={"results": []})
    client.search_in_dictionaries = AsyncMock(return_value={"entries": []})
    client.get_english_translations = AsyncMock(return_value={"translations": []})
    client.get_topic_details = AsyncMock(return_value={"topic": "shabbat"})
    client.clarify_name_argument = AsyncMock(return_value={"suggestions": []})
    client.clarify_search_path_filter = AsyncMock(return_value="Tanakh/Torah/Genesis")
    client.get_library_index = AsyncMock(return_value=[])
    client.get_available_manuscripts = AsyncMock(return_value={"manuscripts": []})
    client.get_manuscript_image = AsyncMock(return_value={"image_url": "http://..."})
    client.set_user_session = Mock()
    client.search_user_source_sheets = AsyncMock(return_value={"sheets": []})
    client.get_source_sheet = AsyncMock(return_value={"sources": []})
    client.create_source_sheet = AsyncMock(return_value={"id": 715437, "sources": []})
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
            ("get_text", {"reference": "Genesis 1:1"}, "get_text", ("Genesis 1:1",)),
            (
                "specific_keyword_search",
                {"query": "shabbat", "filters": "Talmud", "size": 20},
                "text_search",
                ("shabbat", "Talmud", 20),
            ),
            ("specific_keyword_search", {"query": "prayer"}, "text_search", ("prayer", None, 10)),
            ("get_current_calendar", {}, "get_current_calendar", ()),
            (
                "semantic_search",
                {"query": "meaning of life", "filters": "Philosophy"},
                "semantic_search",
                ("meaning of life", "Philosophy", 10),
            ),
            (
                "get_links_between_texts",
                {"reference": "Genesis 1:1", "with_text": "1"},
                "get_links_between_texts",
                ("Genesis 1:1", "1"),
            ),
            (
                "get_links_between_texts",
                {"reference": "Genesis 1:1", "with_text": "1", "category": "Commentary"},
                "get_links_between_texts",
                ("Genesis 1:1", "1", "Commentary"),
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
                "clarify_search_path_filter",
                {"book_name": "Genesis"},
                "clarify_search_path_filter",
                ("Genesis",),
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
            (
                "search_user_source_sheets",
                {"query": "Sabbath prohibitions", "limit": 5},
                "search_user_source_sheets",
                ("Sabbath prohibitions", 5),
            ),
            ("get_source_sheet", {"sheet_id": 702510}, "get_source_sheet", (702510,)),
            (
                "create_source_sheet",
                {
                    "title": "Bereshit",
                    "summary": "A starter sheet",
                    "sources": [{"outsideText": "<p>hi there</p>", "node": 1}],
                },
                "create_source_sheet",
                ("Bereshit", "A starter sheet", [{"outsideText": "<p>hi there</p>", "node": 1}]),
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
    async def test_catalog_get_node_dispatches_to_catalog_service(self, executor):
        executor.catalog_service.get_node = AsyncMock(return_value={"found": True})
        result = await executor.execute(
            "catalog_get_node",
            {"identifier": "Tanakh/Torah", "identifier_type": "path", "child_limit": 5},
        )
        executor.catalog_service.get_node.assert_called_once_with(
            "Tanakh/Torah", identifier_type="path", child_limit=5
        )
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_clarify_search_path_filter_wraps_filter_path(self, executor):
        result = await executor.execute("clarify_search_path_filter", {"book_name": "Genesis"})
        executor.client.clarify_search_path_filter.assert_called_once_with("Genesis")
        assert result.is_error is False
        assert '"filter_path": "Tanakh/Torah/Genesis"' in result.content[0]["text"]

    @pytest.mark.asyncio
    async def test_catalog_get_children_dispatches_to_catalog_service(self, executor):
        executor.catalog_service.get_children = AsyncMock(return_value={"found": True})
        result = await executor.execute(
            "catalog_get_children",
            {"path": "Tanakh", "child_type": "book", "limit": 3, "offset": 2},
        )
        executor.catalog_service.get_children.assert_called_once_with(
            "Tanakh", child_type="book", limit=3, offset=2
        )
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_catalog_search_dispatches_to_catalog_service(self, executor):
        executor.catalog_service.search = AsyncMock(return_value={"results": []})
        result = await executor.execute(
            "catalog_search",
            {"query": "rashi", "node_type": "book", "category_path": "Tanakh", "limit": 4},
        )
        executor.catalog_service.search.assert_called_once_with(
            "rashi", node_type="book", category_path="Tanakh", limit=4
        )
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_catalog_query_dispatches_to_catalog_service(self, executor):
        executor.catalog_service.query = AsyncMock(return_value={"results": []})
        result = await executor.execute(
            "catalog_query",
            {
                "node_type": "book",
                "filters": {"creator": "Rashi"},
                "select": ["title"],
                "limit": 2,
                "offset": 1,
            },
        )
        executor.catalog_service.query.assert_called_once_with(
            node_type="book",
            filters={"creator": "Rashi"},
            select=["title"],
            limit=2,
            offset=1,
        )
        assert result.is_error is False

    def test_set_message_context_sets_client_user_session(self, executor, mock_client):
        from chat.V2.agent import MessageContext

        context = MessageContext(user_id="186013", encrypted_user_token="encrypted-token")

        executor.set_message_context(context)

        mock_client.set_user_session.assert_called_once_with("186013", "encrypted-token")


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
            ("specific_keyword_search", {"query": "shabbat"}, ["Searching texts", "shabbat"]),
            (
                "specific_keyword_search",
                {"query": "prayer", "filters": "Talmud"},
                ["prayer", "Talmud"],
            ),
            ("get_text", {"reference": "Genesis 1:1"}, ["Fetching text", "Genesis 1:1"]),
            (
                "search_user_source_sheets",
                {"query": "halacha workflow"},
                ["user's source sheets", "halacha workflow"],
            ),
            ("get_source_sheet", {"sheet_id": 702510}, ["source sheet", "702510"]),
            (
                "create_source_sheet",
                {"title": "Bereshit"},
                ["Creating source sheet", "Bereshit"],
            ),
            (
                "semantic_search",
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
            (
                "clarify_search_path_filter",
                {"book_name": "Genesis"},
                ["Resolving book filter", "Genesis"],
            ),
            (
                "catalog_get_node",
                {"identifier": "Tanakh/Torah"},
                ["catalog node", "Tanakh/Torah"],
            ),
            (
                "catalog_get_children",
                {"path": "Tanakh"},
                ["catalog children", "Tanakh"],
            ),
            ("catalog_search", {"query": "rashi"}, ["Searching catalog", "rashi"]),
            ("catalog_query", {}, ["Querying cached catalog"]),
            ("unknown_tool", {"arg": "value"}, ["Running tool", "unknown_tool"]),
        ],
    )
    def test_describe_tool(self, tool_name, args, expected_phrases):
        desc = describe_tool_call(tool_name, args)
        for phrase in expected_phrases:
            assert phrase in desc

    def test_truncates_long_values(self):
        long_query = "x" * 200
        desc = describe_tool_call("specific_keyword_search", {"query": long_query})
        assert "..." in desc or "…" in desc
        assert len(desc) < len(long_query) + 50
