"""Tests for Anthropic-compatible chat endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.test import override_settings
from rest_framework.test import APIRequestFactory

from chat.V2.agent import AgentResponse
from chat.V2.anthropic_views import (
    chat_anthropic_v2,
    extract_user_message,
    to_anthropic_response,
)


class TestExtractUserMessage:
    """Test extract_user_message helper function."""

    def test_simple_string_content(self):
        messages = [{"role": "user", "content": "Hello, Claude"}]
        assert extract_user_message(messages) == "Hello, Claude"

    def test_content_blocks_format(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is "},
                    {"type": "text", "text": "Shabbat?"},
                ],
            }
        ]
        assert extract_user_message(messages) == "What is Shabbat?"

    def test_mixed_content_blocks(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "data": "..."}},
                    {"type": "text", "text": "What is this?"},
                ],
            }
        ]
        assert extract_user_message(messages) == "What is this?"

    def test_multiple_messages_returns_last_user(self):
        messages = [
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Second question"},
        ]
        assert extract_user_message(messages) == "Second question"

    def test_no_user_message_returns_empty(self):
        messages = [{"role": "assistant", "content": "Hello"}]
        assert extract_user_message(messages) == ""

    def test_empty_messages_returns_empty(self):
        assert extract_user_message([]) == ""

    def test_string_in_content_blocks(self):
        """Some implementations pass strings directly in content arrays."""
        messages = [{"role": "user", "content": ["Hello", "World"]}]
        assert extract_user_message(messages) == "HelloWorld"

    def test_skips_assistant_messages(self):
        messages = [
            {"role": "assistant", "content": "I am Claude"},
            {"role": "user", "content": "Hello"},
        ]
        assert extract_user_message(messages) == "Hello"


class TestToAnthropicResponse:
    """Test to_anthropic_response helper function."""

    def test_basic_text_response(self):
        agent_response = AgentResponse(
            content="This is the response",
            tool_calls=[],
            llm_calls=1,
            latency_ms=100,
            trace_id="trace_123",
        )
        result = to_anthropic_response(agent_response, "test-model")

        assert result["type"] == "message"
        assert result["role"] == "assistant"
        assert result["model"] == "test-model"
        assert result["stop_reason"] == "end_turn"
        assert result["stop_sequence"] is None
        assert "id" in result
        assert result["id"].startswith("msg_")
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        assert result["content"][0]["text"] == "This is the response"

    def test_response_with_tool_calls(self):
        agent_response = AgentResponse(
            content="Here is what I found",
            tool_calls=[
                {"tool_name": "get_text", "tool_input": {"reference": "Genesis 1:1"}},
                {"tool_name": "text_search", "tool_input": {"query": "shabbat"}},
            ],
            llm_calls=2,
            latency_ms=500,
        )
        result = to_anthropic_response(agent_response, "test-model")

        assert len(result["content"]) == 3
        assert result["content"][0]["type"] == "text"
        assert result["content"][1]["type"] == "tool_use"
        assert result["content"][1]["name"] == "get_text"
        assert result["content"][1]["input"] == {"reference": "Genesis 1:1"}
        assert result["content"][1]["id"].startswith("toolu_")
        assert result["content"][2]["type"] == "tool_use"
        assert result["content"][2]["name"] == "text_search"

    def test_empty_content(self):
        agent_response = AgentResponse(
            content="",
            tool_calls=[],
            llm_calls=1,
            latency_ms=50,
        )
        result = to_anthropic_response(agent_response, "test-model")

        assert len(result["content"]) == 0

    def test_tool_call_with_missing_fields(self):
        agent_response = AgentResponse(
            content="Response",
            tool_calls=[{}],  # Empty tool call
            llm_calls=1,
            latency_ms=100,
        )
        result = to_anthropic_response(agent_response, "test-model")

        assert result["content"][1]["name"] == "unknown"
        assert result["content"][1]["input"] == {}

    def test_usage_fields_present(self):
        agent_response = AgentResponse(
            content="Test",
            tool_calls=[],
            llm_calls=1,
            latency_ms=100,
        )
        result = to_anthropic_response(agent_response, "test-model")

        assert "usage" in result
        assert "input_tokens" in result["usage"]
        assert "output_tokens" in result["usage"]


class TestChatAnthropicEndpoint:
    """Test the chat_anthropic_v2 endpoint."""

    @pytest.fixture
    def factory(self):
        return APIRequestFactory()

    @pytest.fixture
    def mock_agent_response(self):
        return AgentResponse(
            content="Shabbat is the Jewish day of rest.",
            tool_calls=[
                {"tool_name": "get_text", "tool_input": {"reference": "Genesis 2:3"}},
            ],
            llm_calls=1,
            latency_ms=200,
            trace_id="trace_abc123",
        )

    @pytest.fixture
    def mock_agent_service(self, mock_agent_response):
        """Create a mock agent service that returns a predefined response."""
        mock_service = MagicMock()
        mock_service.send_message = AsyncMock(return_value=mock_agent_response)
        return mock_service

    def test_missing_messages_returns_400(self, factory):
        request = factory.post(
            "/api/v2/chat/anthropic",
            data={},
            format="json",
        )
        response = chat_anthropic_v2(request)

        assert response.status_code == 400
        assert response.data["error"]["type"] == "invalid_request_error"
        assert "messages is required" in response.data["error"]["message"]

    def test_empty_messages_returns_400(self, factory):
        request = factory.post(
            "/api/v2/chat/anthropic",
            data={"messages": []},
            format="json",
        )
        response = chat_anthropic_v2(request)

        assert response.status_code == 400

    def test_no_user_message_returns_400(self, factory):
        request = factory.post(
            "/api/v2/chat/anthropic",
            data={"messages": [{"role": "assistant", "content": "Hello"}]},
            format="json",
        )
        response = chat_anthropic_v2(request)

        assert response.status_code == 400
        assert "No user message found" in response.data["error"]["message"]

    @override_settings(CORE_PROMPT_SLUG="test-prompt")
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_successful_request(self, mock_get_agent, factory, mock_agent_service):
        mock_get_agent.return_value = mock_agent_service

        request = factory.post(
            "/api/v2/chat/anthropic",
            data={
                "model": "sefaria-agent",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": "What is Shabbat?"}],
            },
            format="json",
        )
        response = chat_anthropic_v2(request)

        assert response.status_code == 200
        assert response.data["type"] == "message"
        assert response.data["role"] == "assistant"
        assert response.data["model"] == "sefaria-agent"
        assert len(response.data["content"]) == 2  # text + tool_use
        assert response.data["content"][0]["text"] == "Shabbat is the Jewish day of rest."

    @override_settings(CORE_PROMPT_SLUG="default-prompt")
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_uses_custom_prompt_slug_from_metadata(
        self, mock_get_agent, factory, mock_agent_service
    ):
        mock_get_agent.return_value = mock_agent_service

        request = factory.post(
            "/api/v2/chat/anthropic",
            data={
                "messages": [{"role": "user", "content": "Test"}],
                "metadata": {"core_prompt_slug": "custom-prompt"},
            },
            format="json",
        )
        chat_anthropic_v2(request)

        # Verify the agent was called with the custom prompt slug
        call_kwargs = mock_agent_service.send_message.call_args.kwargs
        assert call_kwargs["core_prompt_id"] == "custom-prompt"

    @override_settings(CORE_PROMPT_SLUG="default-prompt")
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_uses_default_prompt_slug_when_not_provided(
        self, mock_get_agent, factory, mock_agent_service
    ):
        mock_get_agent.return_value = mock_agent_service

        request = factory.post(
            "/api/v2/chat/anthropic",
            data={"messages": [{"role": "user", "content": "Test"}]},
            format="json",
        )
        chat_anthropic_v2(request)

        call_kwargs = mock_agent_service.send_message.call_args.kwargs
        assert call_kwargs["core_prompt_id"] == "default-prompt"

    @override_settings(CORE_PROMPT_SLUG="test-prompt")
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_agent_called_without_progress_callback(
        self, mock_get_agent, factory, mock_agent_service
    ):
        mock_get_agent.return_value = mock_agent_service

        request = factory.post(
            "/api/v2/chat/anthropic",
            data={"messages": [{"role": "user", "content": "Test"}]},
            format="json",
        )
        chat_anthropic_v2(request)

        call_kwargs = mock_agent_service.send_message.call_args.kwargs
        assert call_kwargs["on_progress"] is None
        assert call_kwargs["summary_text"] == ""

    @override_settings(CORE_PROMPT_SLUG="test-prompt")
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_agent_error_returns_500(self, mock_get_agent, factory):
        mock_service = MagicMock()
        mock_service.send_message = AsyncMock(side_effect=Exception("Agent exploded"))
        mock_get_agent.return_value = mock_service

        request = factory.post(
            "/api/v2/chat/anthropic",
            data={"messages": [{"role": "user", "content": "Test"}]},
            format="json",
        )
        response = chat_anthropic_v2(request)

        assert response.status_code == 500
        assert response.data["error"]["type"] == "api_error"
        assert "Agent exploded" in response.data["error"]["message"]

    @override_settings(CORE_PROMPT_SLUG="test-prompt")
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_uses_model_from_request(self, mock_get_agent, factory, mock_agent_service):
        mock_get_agent.return_value = mock_agent_service

        request = factory.post(
            "/api/v2/chat/anthropic",
            data={
                "model": "custom-model-name",
                "messages": [{"role": "user", "content": "Test"}],
            },
            format="json",
        )
        response = chat_anthropic_v2(request)

        assert response.data["model"] == "custom-model-name"

    @override_settings(CORE_PROMPT_SLUG="test-prompt")
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_content_blocks_format_input(self, mock_get_agent, factory, mock_agent_service):
        mock_get_agent.return_value = mock_agent_service

        request = factory.post(
            "/api/v2/chat/anthropic",
            data={
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "What is Shabbat?"},
                        ],
                    }
                ]
            },
            format="json",
        )
        response = chat_anthropic_v2(request)

        assert response.status_code == 200
        # Verify the message was extracted correctly
        call_args = mock_agent_service.send_message.call_args.kwargs
        assert call_args["messages"][0].content == "What is Shabbat?"

    @override_settings(CORE_PROMPT_SLUG="test-prompt")
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_multi_turn_conversation_uses_last_user_message(
        self, mock_get_agent, factory, mock_agent_service
    ):
        mock_get_agent.return_value = mock_agent_service

        request = factory.post(
            "/api/v2/chat/anthropic",
            data={
                "messages": [
                    {"role": "user", "content": "First question"},
                    {"role": "assistant", "content": "First answer"},
                    {"role": "user", "content": "Follow-up question"},
                ]
            },
            format="json",
        )
        response = chat_anthropic_v2(request)

        assert response.status_code == 200
        call_args = mock_agent_service.send_message.call_args.kwargs
        assert call_args["messages"][0].content == "Follow-up question"
