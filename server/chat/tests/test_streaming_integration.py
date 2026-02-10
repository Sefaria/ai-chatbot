"""Integration tests for the V2 streaming chat endpoint.

These tests ensure backwards compatibility when refactoring the authentication
and session management code.
"""

import base64
import hashlib
import json
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from chat.models import ChatMessage, ChatSession
from chat.V2.agent import AgentResponse


def create_test_token(user_id: str, secret: str, expires_at=None) -> str:
    """Create a valid encrypted token for testing."""
    if expires_at is None:
        expires_at = timezone.now() + timedelta(hours=1)

    payload = {"id": user_id, "expiration": expires_at.isoformat()}
    payload_bytes = json.dumps(payload).encode("utf-8")

    key = hashlib.sha256(secret.encode("utf-8")).digest()
    aesgcm = AESGCM(key)
    nonce = b"\x00" * 12
    encrypted = aesgcm.encrypt(nonce, payload_bytes, None)

    token_bytes = nonce + encrypted
    return base64.urlsafe_b64encode(token_bytes).decode("ascii").rstrip("=")


@pytest.mark.django_db
class TestStreamingEndpointAuthentication:
    """Tests for authentication in the streaming endpoint."""

    @pytest.fixture
    def client(self):
        return APIClient()

    @pytest.fixture
    def secret(self):
        return "test-secret-key-for-tokens"

    @pytest.fixture
    def valid_request_data(self, secret):
        return {
            "userId": create_test_token("user_123", secret),
            "sessionId": "sess_test123",
            "messageId": "msg_test123",
            "timestamp": timezone.now().isoformat(),
            "text": "Hello, Claude!",
        }

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    @patch("chat.V2.views.get_agent_service")
    def test_valid_token_authenticates_successfully(
        self, mock_get_agent, client, valid_request_data
    ):
        """Valid user token should authenticate and process request."""
        mock_agent = MagicMock()
        mock_agent.send_message = AsyncMock(
            return_value=AgentResponse(
                content="Hello! How can I help?",
                tool_calls=[],
                latency_ms=100,
                trace_id="trace_123",
            )
        )
        mock_get_agent.return_value = mock_agent

        response = client.post(
            "/api/v2/chat/stream",
            data=valid_request_data,
            format="json",
        )

        # Streaming endpoint returns 200 for SSE
        assert response.status_code == 200
        assert response["Content-Type"] == "text/event-stream"

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    def test_expired_token_returns_401(self, client, secret):
        """Expired token should return 401 Unauthorized."""
        expired_time = timezone.now() - timedelta(hours=1)
        request_data = {
            "userId": create_test_token("user_123", secret, expires_at=expired_time),
            "sessionId": "sess_test123",
            "messageId": "msg_test123",
            "timestamp": timezone.now().isoformat(),
            "text": "Hello",
        }

        response = client.post(
            "/api/v2/chat/stream",
            data=request_data,
            format="json",
        )

        assert response.status_code == 401
        assert response.data["error"] == "userId_expired"

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    def test_invalid_token_returns_401(self, client):
        """Invalid/corrupted token should return 401 Unauthorized."""
        request_data = {
            "userId": "invalid-token-not-encrypted",
            "sessionId": "sess_test123",
            "messageId": "msg_test123",
            "timestamp": timezone.now().isoformat(),
            "text": "Hello",
        }

        response = client.post(
            "/api/v2/chat/stream",
            data=request_data,
            format="json",
        )

        assert response.status_code == 401
        assert response.data["error"] == "invalid_userId"

    @override_settings(CHATBOT_USER_TOKEN_SECRET="wrong-secret")
    def test_token_with_wrong_secret_returns_401(self, client, secret):
        """Token encrypted with different secret should return 401."""
        request_data = {
            "userId": create_test_token("user_123", secret),  # Uses different secret
            "sessionId": "sess_test123",
            "messageId": "msg_test123",
            "timestamp": timezone.now().isoformat(),
            "text": "Hello",
        }

        response = client.post(
            "/api/v2/chat/stream",
            data=request_data,
            format="json",
        )

        assert response.status_code == 401
        assert response.data["error"] == "invalid_userId"

    def test_missing_token_returns_400(self, client):
        """Missing userId should return 400 Bad Request."""
        request_data = {
            "sessionId": "sess_test123",
            "messageId": "msg_test123",
            "timestamp": timezone.now().isoformat(),
            "text": "Hello",
        }

        response = client.post(
            "/api/v2/chat/stream",
            data=request_data,
            format="json",
        )

        assert response.status_code == 400

    @override_settings(CHATBOT_USER_TOKEN_SECRET="")
    def test_missing_server_secret_returns_401(self, client, secret):
        """Missing server secret should return 401 since auth cannot proceed."""
        request_data = {
            "userId": create_test_token("user_123", secret),
            "sessionId": "sess_test123",
            "messageId": "msg_test123",
            "timestamp": timezone.now().isoformat(),
            "text": "Hello",
        }

        response = client.post(
            "/api/v2/chat/stream",
            data=request_data,
            format="json",
        )

        assert response.status_code == 401
        assert response.data["error"] == "invalid_userId"


@pytest.mark.django_db
class TestStreamingEndpointSessionManagement:
    """Tests for session creation and management."""

    @pytest.fixture
    def client(self):
        return APIClient()

    @pytest.fixture
    def secret(self):
        return "test-secret-key-for-tokens"

    @pytest.fixture
    def mock_agent(self):
        mock = MagicMock()
        mock.send_message = AsyncMock(
            return_value=AgentResponse(
                content="Hello! How can I help?",
                tool_calls=[],
                latency_ms=100,
                trace_id="trace_123",
            )
        )
        return mock

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    @patch("chat.V2.views.get_agent_service")
    def test_session_created_on_first_message(self, mock_get_agent, client, secret, mock_agent):
        """First message should create a new session."""
        mock_get_agent.return_value = mock_agent

        session_id = "sess_new_session"
        request_data = {
            "userId": create_test_token("user_456", secret),
            "sessionId": session_id,
            "messageId": "msg_first",
            "timestamp": timezone.now().isoformat(),
            "text": "First message",
        }

        # Verify session doesn't exist
        assert not ChatSession.objects.filter(session_id=session_id).exists()

        response = client.post("/api/v2/chat/stream", data=request_data, format="json")
        assert response.status_code == 200

        # Verify session was created
        session = ChatSession.objects.get(session_id=session_id)
        assert session.user_id == "user_456"

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    @patch("chat.V2.views.get_agent_service")
    def test_session_reused_on_subsequent_messages(
        self, mock_get_agent, client, secret, mock_agent
    ):
        """Subsequent messages should reuse existing session."""
        mock_get_agent.return_value = mock_agent

        session_id = "sess_existing"
        user_id = "user_789"

        # Create initial session
        ChatSession.objects.create(session_id=session_id, user_id=user_id)

        request_data = {
            "userId": create_test_token(user_id, secret),
            "sessionId": session_id,
            "messageId": "msg_second",
            "timestamp": timezone.now().isoformat(),
            "text": "Second message",
        }

        response = client.post("/api/v2/chat/stream", data=request_data, format="json")
        assert response.status_code == 200

        # Verify only one session exists
        assert ChatSession.objects.filter(session_id=session_id).count() == 1


@pytest.mark.django_db
class TestStreamingEndpointMessagePersistence:
    """Tests for message persistence to database."""

    @pytest.fixture
    def client(self):
        return APIClient()

    @pytest.fixture
    def secret(self):
        return "test-secret-key-for-tokens"

    @pytest.fixture
    def mock_agent(self):
        mock = MagicMock()
        mock.send_message = AsyncMock(
            return_value=AgentResponse(
                content="This is the assistant response.",
                tool_calls=[{"tool_name": "get_text", "tool_input": {"reference": "Genesis 1:1"}}],
                latency_ms=150,
                trace_id="trace_persist",
            )
        )
        return mock

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    @patch("chat.V2.views.get_agent_service")
    def test_user_message_persisted_to_db(self, mock_get_agent, client, secret, mock_agent):
        """User message should be saved to ChatMessage table."""
        mock_get_agent.return_value = mock_agent

        user_id = "user_persist"
        session_id = "sess_persist"
        message_id = "msg_user_persist"
        message_text = "What is the meaning of Genesis 1:1?"

        request_data = {
            "userId": create_test_token(user_id, secret),
            "sessionId": session_id,
            "messageId": message_id,
            "timestamp": timezone.now().isoformat(),
            "text": message_text,
        }

        response = client.post("/api/v2/chat/stream", data=request_data, format="json")
        assert response.status_code == 200

        # Consume the streaming response
        list(response.streaming_content)

        # Verify user message was saved
        user_msg = ChatMessage.objects.get(message_id=message_id)
        assert user_msg.role == "user"
        assert user_msg.content == message_text
        assert user_msg.user_id == user_id
        assert user_msg.session_id == session_id

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    @patch("chat.V2.views.get_agent_service")
    def test_assistant_message_persisted_to_db(self, mock_get_agent, client, secret, mock_agent):
        """Assistant response should be saved to ChatMessage table."""
        mock_get_agent.return_value = mock_agent

        user_id = "user_assist"
        session_id = "sess_assist"

        request_data = {
            "userId": create_test_token(user_id, secret),
            "sessionId": session_id,
            "messageId": "msg_assist_user",
            "timestamp": timezone.now().isoformat(),
            "text": "Hello",
        }

        response = client.post("/api/v2/chat/stream", data=request_data, format="json")
        assert response.status_code == 200

        # Consume the streaming response
        list(response.streaming_content)

        # Verify assistant message was saved
        assistant_msgs = ChatMessage.objects.filter(session_id=session_id, role="assistant")
        assert assistant_msgs.count() == 1
        assistant_msg = assistant_msgs.first()
        assert assistant_msg.content == "This is the assistant response."
        assert assistant_msg.user_id == user_id

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    @patch("chat.V2.views.get_agent_service")
    def test_user_and_assistant_messages_linked(self, mock_get_agent, client, secret, mock_agent):
        """User message should be linked to assistant response."""
        mock_get_agent.return_value = mock_agent

        user_id = "user_link"
        session_id = "sess_link"
        message_id = "msg_link_user"

        request_data = {
            "userId": create_test_token(user_id, secret),
            "sessionId": session_id,
            "messageId": message_id,
            "timestamp": timezone.now().isoformat(),
            "text": "Link test",
        }

        response = client.post("/api/v2/chat/stream", data=request_data, format="json")
        list(response.streaming_content)

        # Verify linking
        user_msg = ChatMessage.objects.get(message_id=message_id)
        assert user_msg.response_message is not None
        assert user_msg.response_message.role == "assistant"


@pytest.mark.django_db
class TestStreamingEndpointSSEFormat:
    """Tests for Server-Sent Events format."""

    @pytest.fixture
    def client(self):
        return APIClient()

    @pytest.fixture
    def secret(self):
        return "test-secret-key-for-tokens"

    @pytest.fixture
    def mock_agent(self):
        mock = MagicMock()
        mock.send_message = AsyncMock(
            return_value=AgentResponse(
                content="SSE test response",
                tool_calls=[],
                latency_ms=50,
                trace_id="trace_sse",
            )
        )
        return mock

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    @patch("chat.V2.views.get_agent_service")
    def test_response_is_sse_format(self, mock_get_agent, client, secret, mock_agent):
        """Response should be in Server-Sent Events format."""
        mock_get_agent.return_value = mock_agent

        request_data = {
            "userId": create_test_token("user_sse", secret),
            "sessionId": "sess_sse",
            "messageId": "msg_sse",
            "timestamp": timezone.now().isoformat(),
            "text": "SSE test",
        }

        response = client.post("/api/v2/chat/stream", data=request_data, format="json")

        assert response.status_code == 200
        assert response["Content-Type"] == "text/event-stream"
        assert response["Cache-Control"] == "no-cache"

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    @patch("chat.V2.views.get_agent_service")
    def test_sse_contains_message_event(self, mock_get_agent, client, secret, mock_agent):
        """SSE stream should contain a message event with response data."""
        mock_get_agent.return_value = mock_agent

        request_data = {
            "userId": create_test_token("user_sse2", secret),
            "sessionId": "sess_sse2",
            "messageId": "msg_sse2",
            "timestamp": timezone.now().isoformat(),
            "text": "Final message test",
        }

        response = client.post("/api/v2/chat/stream", data=request_data, format="json")

        # Collect all SSE events
        content = b"".join(response.streaming_content).decode("utf-8")

        # Should contain message event
        assert "event: message" in content

        # Parse the message event data
        for line in content.split("\n"):
            if line.startswith("data: ") and "markdown" in line:
                data = json.loads(line[6:])
                assert data["markdown"] == "SSE test response"
                assert "messageId" in data
                assert "sessionId" in data
                break


@pytest.mark.django_db
class TestStreamingEndpointErrorHandling:
    """Tests for error handling in streaming endpoint."""

    @pytest.fixture
    def client(self):
        return APIClient()

    @pytest.fixture
    def secret(self):
        return "test-secret-key-for-tokens"

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    @patch("chat.V2.views.get_agent_service")
    def test_agent_error_returns_error_event(self, mock_get_agent, client, secret):
        """Agent error should return SSE error event."""
        mock_agent = MagicMock()
        mock_agent.send_message = AsyncMock(side_effect=Exception("Agent crashed"))
        mock_get_agent.return_value = mock_agent

        request_data = {
            "userId": create_test_token("user_error", secret),
            "sessionId": "sess_error",
            "messageId": "msg_error",
            "timestamp": timezone.now().isoformat(),
            "text": "This will fail",
        }

        response = client.post("/api/v2/chat/stream", data=request_data, format="json")

        # Still returns 200 for SSE (error is in the stream)
        assert response.status_code == 200

        content = b"".join(response.streaming_content).decode("utf-8")
        assert "event: error" in content

    def test_missing_required_fields_returns_400(self, client):
        """Missing required fields should return 400."""
        response = client.post(
            "/api/v2/chat/stream",
            data={"userId": "token", "sessionId": "sess"},  # Missing text, messageId, timestamp
            format="json",
        )

        assert response.status_code == 400
        assert "error" in response.data

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    def test_text_too_long_returns_400(self, client, secret):
        """Text exceeding max length should return 400."""
        request_data = {
            "userId": create_test_token("user_long", secret),
            "sessionId": "sess_long",
            "messageId": "msg_long",
            "timestamp": timezone.now().isoformat(),
            "text": "x" * 10001,  # Max is 10000
        }

        response = client.post("/api/v2/chat/stream", data=request_data, format="json")

        assert response.status_code == 400
