"""Tests for the evaluation script."""

import pytest


class TestChatbotClientExtractText:
    """Tests for ChatbotClient.extract_text method."""

    @pytest.fixture
    def client(self):
        # Import here to avoid import errors when server isn't available
        import sys
        import os

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from evals.run_eval import ChatbotClient

        return ChatbotClient.__new__(ChatbotClient)

    def test_extracts_single_text_block(self, client):
        """Should extract text from a single text block."""
        response = {"content": [{"type": "text", "text": "Hello world"}]}
        assert client.extract_text(response) == "Hello world"

    def test_extracts_multiple_text_blocks(self, client):
        """Should join multiple text blocks with newlines."""
        response = {
            "content": [
                {"type": "text", "text": "First"},
                {"type": "text", "text": "Second"},
            ]
        }
        assert client.extract_text(response) == "First\nSecond"

    def test_ignores_non_text_blocks(self, client):
        """Should skip tool_use and other block types."""
        response = {
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "tool_use", "id": "123", "name": "search"},
                {"type": "text", "text": "World"},
            ]
        }
        assert client.extract_text(response) == "Hello\nWorld"

    def test_handles_empty_content(self, client):
        """Should return empty string for empty content."""
        assert client.extract_text({"content": []}) == ""
        assert client.extract_text({}) == ""

    def test_handles_missing_text_field(self, client):
        """Should handle text blocks missing the text field."""
        response = {"content": [{"type": "text"}]}
        assert client.extract_text(response) == ""


class TestChatbotClientExtractToolCalls:
    """Tests for ChatbotClient.extract_tool_calls method."""

    @pytest.fixture
    def client(self):
        import sys
        import os

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from evals.run_eval import ChatbotClient

        return ChatbotClient.__new__(ChatbotClient)

    def test_extracts_tool_use_blocks(self, client):
        """Should extract only tool_use blocks."""
        response = {
            "content": [
                {"type": "text", "text": "Let me search"},
                {"type": "tool_use", "id": "1", "name": "search", "input": {}},
                {"type": "text", "text": "Found it"},
            ]
        }
        tools = client.extract_tool_calls(response)
        assert len(tools) == 1
        assert tools[0]["name"] == "search"

    def test_returns_empty_list_when_no_tools(self, client):
        """Should return empty list when no tool calls."""
        response = {"content": [{"type": "text", "text": "No tools"}]}
        assert client.extract_tool_calls(response) == []
