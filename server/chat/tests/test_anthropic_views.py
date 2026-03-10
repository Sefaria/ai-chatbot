"""Tests for Anthropic-compatible chat endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.test import override_settings
from rest_framework.test import APIClient, APIRequestFactory

from chat.tests.test_streaming_integration import create_test_token
from chat.V2.agent import AgentResponse
from chat.V2.anthropic_views import (
    BRAINTRUST_ORIGIN,
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

    @pytest.fixture
    def default_stats(self):
        return {"llmCalls": None, "toolCalls": 0, "latencyMs": 100}

    def test_basic_text_response(self, default_stats):
        agent_response = AgentResponse(
            content="This is the response",
            tool_calls=[],
            latency_ms=100,
            trace_id="trace_123",
        )
        result = to_anthropic_response(agent_response, "test-model", "msg_test123", default_stats)

        assert result["type"] == "message"
        assert result["role"] == "assistant"
        assert result["model"] == "test-model"
        assert result["stop_reason"] == "end_turn"
        assert result["stop_sequence"] is None
        assert result["id"] == "msg_test123"
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        assert result["content"][0]["text"] == "This is the response"
        # Check metadata includes origin and trace_id
        assert result["metadata"]["origin"] == BRAINTRUST_ORIGIN
        assert result["metadata"]["trace_id"] == "trace_123"

    def test_response_with_tool_calls(self, default_stats):
        agent_response = AgentResponse(
            content="Here is what I found",
            tool_calls=[
                {"tool_name": "get_text", "tool_input": {"reference": "Genesis 1:1"}},
                {"tool_name": "text_search", "tool_input": {"query": "shabbat"}},
            ],
            latency_ms=500,
        )
        result = to_anthropic_response(agent_response, "test-model", "msg_test123", default_stats)

        assert len(result["content"]) == 3
        assert result["content"][0]["type"] == "text"
        assert result["content"][1]["type"] == "tool_use"
        assert result["content"][1]["name"] == "get_text"
        assert result["content"][1]["input"] == {"reference": "Genesis 1:1"}
        assert result["content"][1]["id"].startswith("toolu_")
        assert result["content"][2]["type"] == "tool_use"
        assert result["content"][2]["name"] == "text_search"

    def test_empty_content(self, default_stats):
        agent_response = AgentResponse(
            content="",
            tool_calls=[],
            latency_ms=50,
        )
        result = to_anthropic_response(agent_response, "test-model", "msg_test123", default_stats)

        assert len(result["content"]) == 0

    def test_tool_call_with_missing_fields(self, default_stats):
        agent_response = AgentResponse(
            content="Response",
            tool_calls=[{}],  # Empty tool call
            latency_ms=100,
        )
        result = to_anthropic_response(agent_response, "test-model", "msg_test123", default_stats)

        assert result["content"][1]["name"] == "unknown"
        assert result["content"][1]["input"] == {}

    def test_usage_fields_present(self, default_stats):
        agent_response = AgentResponse(
            content="Test",
            tool_calls=[],
            latency_ms=100,
        )
        result = to_anthropic_response(agent_response, "test-model", "msg_test123", default_stats)

        assert "usage" in result
        assert "input_tokens" in result["usage"]
        assert "output_tokens" in result["usage"]


@pytest.mark.django_db
class TestChatAnthropicEndpoint:
    """Test the chat_anthropic_v2 endpoint."""

    @pytest.fixture
    def factory(self):
        return APIRequestFactory()

    @pytest.fixture
    def secret(self):
        return "test-secret-key"

    @pytest.fixture
    def user_token(self, secret):
        """Create a valid user token for authentication."""
        return create_test_token("test-user", secret)

    @pytest.fixture
    def mock_agent_response(self):
        return AgentResponse(
            content="Shabbat is the Jewish day of rest.",
            tool_calls=[
                {"tool_name": "get_text", "tool_input": {"reference": "Genesis 2:3"}},
            ],
            latency_ms=200,
            trace_id="trace_abc123",
        )

    @pytest.fixture
    def mock_agent_service(self, mock_agent_response):
        """Create a mock agent service that returns a predefined response."""
        mock_service = MagicMock()
        mock_service.send_message = AsyncMock(return_value=mock_agent_response)
        return mock_service

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    def test_missing_messages_returns_400(self, factory, user_token):
        request = factory.post(
            "/api/v2/chat/anthropic",
            data={},
            format="json",
            HTTP_X_API_KEY=user_token,
        )
        response = chat_anthropic_v2(request)

        assert response.status_code == 400
        assert response.data["error"]["type"] == "invalid_request_error"
        assert "messages" in response.data["error"]["message"]

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    def test_empty_messages_returns_400(self, factory, user_token):
        request = factory.post(
            "/api/v2/chat/anthropic",
            data={"messages": []},
            format="json",
            HTTP_X_API_KEY=user_token,
        )
        response = chat_anthropic_v2(request)

        assert response.status_code == 400

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    def test_no_user_message_returns_400(self, factory, user_token):
        request = factory.post(
            "/api/v2/chat/anthropic",
            data={"messages": [{"role": "assistant", "content": "Hello"}]},
            format="json",
            HTTP_X_API_KEY=user_token,
        )
        response = chat_anthropic_v2(request)

        assert response.status_code == 400
        assert "No user message found" in response.data["error"]["message"]

    @override_settings(CORE_PROMPT_SLUG="test-prompt", CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_successful_request(self, mock_get_agent, factory, mock_agent_service, user_token):
        mock_get_agent.return_value = mock_agent_service

        request = factory.post(
            "/api/v2/chat/anthropic",
            data={
                "model": "sefaria-agent",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": "What is Shabbat?"}],
            },
            format="json",
            HTTP_X_API_KEY=user_token,
        )
        response = chat_anthropic_v2(request)

        assert response.status_code == 200
        assert response.data["type"] == "message"
        assert response.data["role"] == "assistant"
        assert response.data["model"] == "sefaria-agent"
        assert len(response.data["content"]) == 2  # text + tool_use
        assert response.data["content"][0]["text"] == "Shabbat is the Jewish day of rest."
        # Check origin metadata
        assert response.data["metadata"]["origin"] == BRAINTRUST_ORIGIN

    @override_settings(
        CORE_PROMPT_SLUG="default-prompt", CHATBOT_USER_TOKEN_SECRET="test-secret-key"
    )
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_uses_custom_prompt_slug_from_metadata(
        self, mock_get_agent, factory, mock_agent_service, user_token
    ):
        mock_get_agent.return_value = mock_agent_service

        request = factory.post(
            "/api/v2/chat/anthropic",
            data={
                "messages": [{"role": "user", "content": "Test"}],
                "metadata": {"core_prompt_slug": "custom-prompt"},
            },
            format="json",
            HTTP_X_API_KEY=user_token,
        )
        chat_anthropic_v2(request)

        # Verify the agent was called with the custom prompt slug
        call_kwargs = mock_agent_service.send_message.call_args.kwargs
        assert call_kwargs["core_prompt_id"] == "custom-prompt"

    @override_settings(
        CORE_PROMPT_SLUG="default-prompt", CHATBOT_USER_TOKEN_SECRET="test-secret-key"
    )
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_uses_default_prompt_slug_when_not_provided(
        self, mock_get_agent, factory, mock_agent_service, user_token
    ):
        mock_get_agent.return_value = mock_agent_service

        request = factory.post(
            "/api/v2/chat/anthropic",
            data={"messages": [{"role": "user", "content": "Test"}]},
            format="json",
            HTTP_X_API_KEY=user_token,
        )
        chat_anthropic_v2(request)

        call_kwargs = mock_agent_service.send_message.call_args.kwargs
        assert call_kwargs["core_prompt_id"] == "default-prompt"

    @override_settings(CORE_PROMPT_SLUG="test-prompt", CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_agent_called_without_progress_callback(
        self, mock_get_agent, factory, mock_agent_service, user_token
    ):
        mock_get_agent.return_value = mock_agent_service

        request = factory.post(
            "/api/v2/chat/anthropic",
            data={"messages": [{"role": "user", "content": "Test"}]},
            format="json",
            HTTP_X_API_KEY=user_token,
        )
        chat_anthropic_v2(request)

        call_kwargs = mock_agent_service.send_message.call_args.kwargs
        assert call_kwargs["on_progress"] is None
        # Stateless mode (no X-Session-ID) means no summary
        assert call_kwargs["context"].summary_text is None

    @override_settings(CORE_PROMPT_SLUG="test-prompt", CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_agent_error_returns_500(self, mock_get_agent, factory, user_token):
        mock_service = MagicMock()
        mock_service.send_message = AsyncMock(side_effect=Exception("Agent exploded"))
        mock_get_agent.return_value = mock_service

        request = factory.post(
            "/api/v2/chat/anthropic",
            data={"messages": [{"role": "user", "content": "Test"}]},
            format="json",
            HTTP_X_API_KEY=user_token,
        )
        with patch("chat.V2.anthropic_views.capture_exception") as mock_capture:
            response = chat_anthropic_v2(request)

        assert response.status_code == 500
        mock_capture.assert_called_once()
        assert response.data["error"]["type"] == "api_error"
        assert response.data["error"]["message"] == "An internal error occurred."
        # Error response should also have metadata
        assert response.data["metadata"]["origin"] == BRAINTRUST_ORIGIN

    @override_settings(CORE_PROMPT_SLUG="test-prompt", CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_uses_model_from_request(self, mock_get_agent, factory, mock_agent_service, user_token):
        mock_get_agent.return_value = mock_agent_service

        request = factory.post(
            "/api/v2/chat/anthropic",
            data={
                "model": "custom-model-name",
                "messages": [{"role": "user", "content": "Test"}],
            },
            format="json",
            HTTP_X_API_KEY=user_token,
        )
        response = chat_anthropic_v2(request)

        assert response.data["model"] == "custom-model-name"

    @override_settings(CORE_PROMPT_SLUG="test-prompt", CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_content_blocks_format_input(
        self, mock_get_agent, factory, mock_agent_service, user_token
    ):
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
                ],
            },
            format="json",
            HTTP_X_API_KEY=user_token,
        )
        response = chat_anthropic_v2(request)

        assert response.status_code == 200
        # Verify the message was extracted correctly
        call_args = mock_agent_service.send_message.call_args.kwargs
        assert call_args["messages"][0].content == "What is Shabbat?"

    @override_settings(CORE_PROMPT_SLUG="test-prompt", CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_multi_turn_conversation_uses_last_user_message(
        self, mock_get_agent, factory, mock_agent_service, user_token
    ):
        mock_get_agent.return_value = mock_agent_service

        request = factory.post(
            "/api/v2/chat/anthropic",
            data={
                "messages": [
                    {"role": "user", "content": "First question"},
                    {"role": "assistant", "content": "First answer"},
                    {"role": "user", "content": "Follow-up question"},
                ],
            },
            format="json",
            HTTP_X_API_KEY=user_token,
        )
        response = chat_anthropic_v2(request)

        assert response.status_code == 200
        call_args = mock_agent_service.send_message.call_args.kwargs
        assert call_args["messages"][0].content == "Follow-up question"

    @override_settings(CORE_PROMPT_SLUG="test-prompt", CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_x_session_id_header_enables_multi_turn(
        self, mock_get_agent, factory, mock_agent_service, user_token
    ):
        """Test that X-Session-ID header creates a consistent session for multi-turn."""
        mock_get_agent.return_value = mock_agent_service

        request = factory.post(
            "/api/v2/chat/anthropic",
            data={"messages": [{"role": "user", "content": "Test"}]},
            format="json",
            HTTP_X_API_KEY=user_token,
            HTTP_X_SESSION_ID="my-multi-turn-session",
        )
        response = chat_anthropic_v2(request)

        assert response.status_code == 200
        # The session should be created with the exact ID from header
        from chat.models import ChatSession

        session = ChatSession.objects.get(session_id="my-multi-turn-session")
        assert session.user_id == "test-user"
        assert session.current_flow == BRAINTRUST_ORIGIN


@pytest.mark.django_db
class TestChatAnthropicHTTPIntegration:
    """Integration tests using Django test client for full HTTP simulation."""

    @pytest.fixture
    def client(self):
        return APIClient()

    @pytest.fixture
    def secret(self):
        return "test-secret-key"

    @pytest.fixture
    def user_token(self, secret):
        """Create a valid user token for authentication."""
        return create_test_token("test-user", secret)

    @pytest.fixture
    def mock_agent_response(self):
        return AgentResponse(
            content="Shabbat is the Jewish day of rest.",
            tool_calls=[
                {"tool_name": "get_text", "tool_input": {"reference": "Genesis 2:3"}},
            ],
            latency_ms=200,
            trace_id="trace_abc123",
        )

    @pytest.fixture
    def mock_agent_service(self, mock_agent_response):
        mock_service = MagicMock()
        mock_service.send_message = AsyncMock(return_value=mock_agent_response)
        return mock_service

    @override_settings(CORE_PROMPT_SLUG="test-prompt", CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_full_http_request_response_cycle(
        self, mock_get_agent, client, mock_agent_service, user_token
    ):
        """Test actual HTTP POST with JSON body and response parsing."""
        mock_get_agent.return_value = mock_agent_service

        response = client.post(
            "/api/v2/chat/anthropic",
            data={"messages": [{"role": "user", "content": "What is Shabbat?"}]},
            format="json",
            HTTP_X_API_KEY=user_token,
        )

        assert response.status_code == 200
        assert response["Content-Type"] == "application/json"
        # APIClient automatically parses JSON
        assert response.data["type"] == "message"
        assert response.data["role"] == "assistant"
        assert "id" in response.data
        assert response.data["id"].startswith("msg_")

    @override_settings(CORE_PROMPT_SLUG="test-prompt", CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_hebrew_text_in_request(self, mock_get_agent, client, mock_agent_service, user_token):
        """Test that Hebrew/Unicode text is handled correctly through HTTP."""
        mock_get_agent.return_value = mock_agent_service

        response = client.post(
            "/api/v2/chat/anthropic",
            data={"messages": [{"role": "user", "content": "מה זה שבת?"}]},
            format="json",
            HTTP_X_API_KEY=user_token,
        )

        assert response.status_code == 200
        # Verify the Hebrew text was passed to the agent
        call_args = mock_agent_service.send_message.call_args.kwargs
        assert call_args["messages"][0].content == "מה זה שבת?"

    @override_settings(CORE_PROMPT_SLUG="test-prompt", CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_large_message_handling(self, mock_get_agent, client, mock_agent_service, user_token):
        """Test handling of large message content."""
        mock_get_agent.return_value = mock_agent_service
        large_content = "Test message. " * 1000  # ~13KB of text

        response = client.post(
            "/api/v2/chat/anthropic",
            data={"messages": [{"role": "user", "content": large_content}]},
            format="json",
            HTTP_X_API_KEY=user_token,
        )

        assert response.status_code == 200
        call_args = mock_agent_service.send_message.call_args.kwargs
        assert call_args["messages"][0].content == large_content

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    def test_invalid_json_returns_400(self, client, user_token):
        """Test that malformed JSON returns a proper error."""
        response = client.post(
            "/api/v2/chat/anthropic",
            data="not valid json",
            content_type="application/json",
            HTTP_X_API_KEY=user_token,
        )

        assert response.status_code == 400

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    def test_missing_content_type_still_works(self, client, user_token):
        """Test that request without explicit Content-Type header works."""
        response = client.post(
            "/api/v2/chat/anthropic",
            data={"messages": []},
            format="json",
            HTTP_X_API_KEY=user_token,
        )

        # Should get 400 for empty messages, not a content-type error
        assert response.status_code == 400
        assert "messages" in str(response.data["error"]["message"]).lower()

    @override_settings(CORE_PROMPT_SLUG="test-prompt", CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_response_includes_all_anthropic_fields(
        self, mock_get_agent, client, mock_agent_service, user_token
    ):
        """Test that response includes all required Anthropic API fields."""
        mock_get_agent.return_value = mock_agent_service

        response = client.post(
            "/api/v2/chat/anthropic",
            data={
                "model": "sefaria-agent",
                "messages": [{"role": "user", "content": "Test"}],
            },
            format="json",
            HTTP_X_API_KEY=user_token,
        )

        assert response.status_code == 200
        data = response.data

        # Required Anthropic response fields
        assert data["type"] == "message"
        assert data["role"] == "assistant"
        assert data["model"] == "sefaria-agent"
        assert data["stop_reason"] == "end_turn"
        assert data["stop_sequence"] is None
        assert "id" in data
        assert "content" in data
        assert "usage" in data
        assert "input_tokens" in data["usage"]
        assert "output_tokens" in data["usage"]
        # Additional metadata
        assert "metadata" in data
        assert data["metadata"]["origin"] == BRAINTRUST_ORIGIN

    @override_settings(CORE_PROMPT_SLUG="test-prompt", CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_tool_calls_in_response(self, mock_get_agent, client, mock_agent_service, user_token):
        """Test that tool calls are properly serialized in HTTP response."""
        mock_get_agent.return_value = mock_agent_service

        response = client.post(
            "/api/v2/chat/anthropic",
            data={"messages": [{"role": "user", "content": "Test"}]},
            format="json",
            HTTP_X_API_KEY=user_token,
        )

        assert response.status_code == 200
        content = response.data["content"]

        # Should have text block and tool_use block
        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "tool_use"
        assert content[1]["name"] == "get_text"
        assert "id" in content[1]
        assert content[1]["id"].startswith("toolu_")

    @override_settings(CORE_PROMPT_SLUG="test-prompt", CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_metadata_passed_through_http(
        self, mock_get_agent, client, mock_agent_service, user_token
    ):
        """Test that metadata is properly passed through HTTP layer."""
        mock_get_agent.return_value = mock_agent_service

        response = client.post(
            "/api/v2/chat/anthropic",
            data={
                "messages": [{"role": "user", "content": "Test"}],
                "metadata": {"core_prompt_slug": "custom-prompt"},
            },
            format="json",
            HTTP_X_API_KEY=user_token,
        )

        assert response.status_code == 200
        call_kwargs = mock_agent_service.send_message.call_args.kwargs
        assert call_kwargs["core_prompt_id"] == "custom-prompt"

    @override_settings(CORE_PROMPT_SLUG="test-prompt", CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_error_response_format(self, mock_get_agent, client, user_token):
        """Test that errors follow Anthropic error response format."""
        mock_service = MagicMock()
        mock_service.send_message = AsyncMock(side_effect=Exception("Test error"))
        mock_get_agent.return_value = mock_service

        response = client.post(
            "/api/v2/chat/anthropic",
            data={"messages": [{"role": "user", "content": "Test"}]},
            format="json",
            HTTP_X_API_KEY=user_token,
        )

        assert response.status_code == 500
        assert "error" in response.data
        assert response.data["error"]["type"] == "api_error"
        assert response.data["error"]["message"] == "An internal error occurred."

    @override_settings(CORE_PROMPT_SLUG="test-prompt", CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    @patch("chat.V2.anthropic_views.get_agent_service")
    def test_messages_logged_to_database(
        self, mock_get_agent, client, mock_agent_service, user_token
    ):
        """Test that user and assistant messages are logged to the database."""
        mock_get_agent.return_value = mock_agent_service

        response = client.post(
            "/api/v2/chat/anthropic",
            data={"messages": [{"role": "user", "content": "What is Shabbat?"}]},
            format="json",
            HTTP_X_API_KEY=user_token,
        )

        assert response.status_code == 200

        # Verify messages were saved - filter by user_id since we're using user token auth
        from chat.models import ChatMessage

        messages = ChatMessage.objects.filter(user_id="test-user")
        assert messages.count() == 2  # User + Assistant
        user_msg = messages.filter(role="user").first()
        assert user_msg.content == "What is Shabbat?"
        assert user_msg.flow == BRAINTRUST_ORIGIN
        assistant_msg = messages.filter(role="assistant").first()
        assert assistant_msg.content == "Shabbat is the Jewish day of rest."
