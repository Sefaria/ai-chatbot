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

        with patch("chat.V2.views.capture_exception") as mock_capture:
            response = client.post("/api/v2/chat/stream", data=request_data, format="json")

            # Still returns 200 for SSE (error is in the stream)
            assert response.status_code == 200

            content = b"".join(response.streaming_content).decode("utf-8")
            mock_capture.assert_called_once()
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


@pytest.mark.django_db
class TestStreamingEndpointOriginPropagation:
    """Tests for origin field propagation through the streaming endpoint."""

    @pytest.fixture
    def client(self):
        from rest_framework.test import APIClient

        return APIClient()

    @pytest.fixture
    def secret(self):
        return "test-secret-key-for-tokens"

    @pytest.fixture
    def mock_agent(self):
        mock = MagicMock()
        mock.send_message = AsyncMock(
            return_value=AgentResponse(
                content="Response",
                tool_calls=[],
                latency_ms=100,
                trace_id="trace_123",
            )
        )
        return mock

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    @patch("chat.V2.views.get_agent_service")
    def test_origin_from_context_propagates_to_agent(
        self, mock_get_agent, client, secret, mock_agent
    ):
        """Origin in request body context should propagate to the agent MessageContext."""

        mock_get_agent.return_value = mock_agent

        response = client.post(
            "/api/v2/chat/stream",
            data={
                "userId": create_test_token("user_origin", secret),
                "sessionId": "sess_origin_test",
                "messageId": "msg_origin_test",
                "timestamp": timezone.now().isoformat(),
                "text": "Hello",
                "context": {"origin": "eval"},
            },
            format="json",
        )

        assert response.status_code == 200
        list(response.streaming_content)  # consume to run the SSE generator

        ctx = mock_agent.send_message.call_args.kwargs["context"]
        assert ctx.origin == "eval"

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    @patch("chat.V2.views.get_agent_service")
    def test_missing_origin_defaults_to_dev(self, mock_get_agent, client, secret, mock_agent):
        """Missing origin in context should default to DEFAULT_ORIGIN."""
        from chat.V2.origin import DEFAULT_ORIGIN

        mock_get_agent.return_value = mock_agent

        response = client.post(
            "/api/v2/chat/stream",
            data={
                "userId": create_test_token("user_noorigin", secret),
                "sessionId": "sess_noorigin_test",
                "messageId": "msg_noorigin_test",
                "timestamp": timezone.now().isoformat(),
                "text": "Hello",
                "context": {},
            },
            format="json",
        )

        assert response.status_code == 200
        list(response.streaming_content)

        ctx = mock_agent.send_message.call_args.kwargs["context"]
        assert ctx.origin == DEFAULT_ORIGIN


@pytest.mark.django_db
class TestStreamingEndpointIsStaffPropagation:
    """Tests for is_staff field propagation through the streaming endpoint."""

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
                content="Response",
                tool_calls=[],
                latency_ms=100,
                trace_id="trace_123",
            )
        )
        return mock

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    @patch("chat.V2.views.get_agent_service")
    def test_is_staff_true_propagates_to_agent(self, mock_get_agent, client, secret, mock_agent):
        """isStaff=true in context should propagate to MessageContext.is_staff."""
        mock_get_agent.return_value = mock_agent

        response = client.post(
            "/api/v2/chat/stream",
            data={
                "userId": create_test_token("user_staff", secret),
                "sessionId": "sess_staff_test",
                "messageId": "msg_staff_test",
                "timestamp": timezone.now().isoformat(),
                "text": "Hello",
                "context": {"isStaff": True},
            },
            format="json",
        )

        assert response.status_code == 200
        list(response.streaming_content)

        ctx = mock_agent.send_message.call_args.kwargs["context"]
        assert ctx.is_staff is True

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    @patch("chat.V2.views.get_agent_service")
    def test_missing_is_staff_defaults_to_false(self, mock_get_agent, client, secret, mock_agent):
        """Missing isStaff in context should default to False."""
        mock_get_agent.return_value = mock_agent

        response = client.post(
            "/api/v2/chat/stream",
            data={
                "userId": create_test_token("user_nostaff", secret),
                "sessionId": "sess_nostaff_test",
                "messageId": "msg_nostaff_test",
                "timestamp": timezone.now().isoformat(),
                "text": "Hello",
                "context": {},
            },
            format="json",
        )

        assert response.status_code == 200
        list(response.streaming_content)

        ctx = mock_agent.send_message.call_args.kwargs["context"]
        assert ctx.is_staff is False

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    @patch("chat.V2.views.get_agent_service")
    def test_user_token_and_user_id_propagate_to_agent_context(
        self, mock_get_agent, client, secret, mock_agent
    ):
        """The agent context should retain both user_id and the encrypted user token."""
        user_token = create_test_token("186013", secret)
        mock_get_agent.return_value = mock_agent

        response = client.post(
            "/api/v2/chat/stream",
            data={
                "userId": user_token,
                "sessionId": "sess_user_context_test",
                "messageId": "msg_user_context_test",
                "timestamp": timezone.now().isoformat(),
                "text": "Hello",
                "context": {},
            },
            format="json",
        )

        assert response.status_code == 200
        list(response.streaming_content)

        ctx = mock_agent.send_message.call_args.kwargs["context"]
        assert ctx.user_id == "186013"
        assert ctx.encrypted_user_token == user_token


@pytest.mark.django_db
class TestStreamingEndpointRecovery:
    """Tests for persisted-response recovery after stream interruption."""

    @pytest.fixture
    def client(self):
        return APIClient()

    @pytest.fixture
    def secret(self):
        return "test-secret-key-for-tokens"

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    def test_summary_failure_does_not_block_final_message(self, client, secret):
        """Assistant response should still stream when summary update fails."""
        mock_agent = MagicMock()
        mock_agent.send_message = AsyncMock(
            return_value=AgentResponse(
                content="Recovered despite summary failure",
                tool_calls=[],
                latency_ms=80,
                trace_id="trace_summary_fail",
            )
        )

        request_data = {
            "userId": create_test_token("user_summary_fail", secret),
            "sessionId": "sess_summary_fail",
            "messageId": "msg_summary_fail",
            "timestamp": timezone.now().isoformat(),
            "text": "Hello",
        }

        with patch("chat.V2.views.get_agent_service", return_value=mock_agent):
            with patch("chat.V2.views.get_summary_service") as mock_summary_service:
                with patch("chat.V2.views.capture_exception") as mock_capture:
                    mock_summary_service.return_value.update_summary.side_effect = Exception(
                        "summary db error"
                    )

                    response = client.post("/api/v2/chat/stream", data=request_data, format="json")
                    content = b"".join(response.streaming_content).decode("utf-8")

        assert response.status_code == 200
        assert "event: message" in content
        assert "Recovered despite summary failure" in content
        mock_capture.assert_called_once()

        session = ChatSession.objects.get(session_id="sess_summary_fail")
        user_msg = ChatMessage.objects.get(message_id="msg_summary_fail")
        assert session.summary_updated_at is None
        assert user_msg.response_message is not None
        assert user_msg.response_message.content == "Recovered despite summary failure"

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    def test_recovery_endpoint_returns_linked_response(self, client, secret):
        """Recovery endpoint should return an already linked assistant response."""
        session = ChatSession.objects.create(session_id="sess_recover", user_id="user_recover")
        user_msg = ChatMessage.objects.create(
            message_id="msg_recover",
            session_id=session.session_id,
            user_id=session.user_id,
            turn_id="turn_recover",
            role=ChatMessage.Role.USER,
            content="Recover me",
        )
        assistant_msg = ChatMessage.objects.create(
            message_id="msg_recover_assistant",
            session_id=session.session_id,
            user_id=session.user_id,
            turn_id="turn_recover",
            role=ChatMessage.Role.ASSISTANT,
            content="Recovered response",
            latency_ms=123,
            tool_calls_count=0,
            status=ChatMessage.Status.SUCCESS,
        )
        user_msg.response_message = assistant_msg
        user_msg.save(update_fields=["response_message"])

        response = client.post(
            "/api/v2/chat/recover",
            data={
                "userId": create_test_token("user_recover", secret),
                "sessionId": "sess_recover",
                "messageId": "msg_recover",
            },
            format="json",
        )

        assert response.status_code == 200
        assert response.data["status"] == "complete"
        assert response.data["message"]["markdown"] == "Recovered response"
        assert response.data["message"]["recovered"] is True

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    def test_recovery_endpoint_links_response_by_turn(self, client, secret):
        """Recovery endpoint should repair linkage when assistant row exists by turn."""
        session = ChatSession.objects.create(session_id="sess_turn_recover", user_id="user_turn")
        user_msg = ChatMessage.objects.create(
            message_id="msg_turn_recover",
            session_id=session.session_id,
            user_id=session.user_id,
            turn_id="turn_turn_recover",
            role=ChatMessage.Role.USER,
            content="Recover by turn",
        )
        assistant_msg = ChatMessage.objects.create(
            message_id="msg_turn_assistant",
            session_id=session.session_id,
            user_id=session.user_id,
            turn_id="turn_turn_recover",
            role=ChatMessage.Role.ASSISTANT,
            content="Recovered by turn",
            latency_ms=77,
            tool_calls_count=0,
            status=ChatMessage.Status.SUCCESS,
        )

        response = client.post(
            "/api/v2/chat/recover",
            data={
                "userId": create_test_token("user_turn", secret),
                "sessionId": "sess_turn_recover",
                "messageId": "msg_turn_recover",
            },
            format="json",
        )

        assert response.status_code == 200
        assert response.data["status"] == "complete"
        assert response.data["message"]["messageId"] == assistant_msg.message_id

        user_msg.refresh_from_db()
        assert user_msg.response_message_id == assistant_msg.id

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    def test_recovery_endpoint_reports_running_when_heartbeat_is_fresh(self, client, secret):
        """Recovery should expose a live running state while heartbeat is fresh."""
        session = ChatSession.objects.create(session_id="sess_running", user_id="user_running")
        ChatMessage.objects.create(
            message_id="msg_running",
            session_id=session.session_id,
            user_id=session.user_id,
            turn_id="turn_running",
            role=ChatMessage.Role.USER,
            content="Still running",
            processing_state=ChatMessage.ProcessingState.RUNNING,
            processing_started_at=timezone.now() - timedelta(seconds=5),
            processing_heartbeat_at=timezone.now() - timedelta(seconds=2),
        )

        response = client.post(
            "/api/v2/chat/recover",
            data={
                "userId": create_test_token("user_running", secret),
                "sessionId": "sess_running",
                "messageId": "msg_running",
            },
            format="json",
        )

        assert response.status_code == 200
        assert response.data["status"] == "running"
        assert response.data["requestFound"] is True
        assert response.data["heartbeatTimeoutMs"] == 90000
        assert response.data["heartbeatAgeMs"] < response.data["heartbeatTimeoutMs"]

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    def test_recovery_endpoint_reports_stale_when_heartbeat_expires(self, client, secret):
        """Recovery should stop polling once the server-side heartbeat is stale."""
        session = ChatSession.objects.create(session_id="sess_stale", user_id="user_stale")
        ChatMessage.objects.create(
            message_id="msg_stale",
            session_id=session.session_id,
            user_id=session.user_id,
            turn_id="turn_stale",
            role=ChatMessage.Role.USER,
            content="Went stale",
            processing_state=ChatMessage.ProcessingState.RUNNING,
            processing_started_at=timezone.now() - timedelta(minutes=3),
            processing_heartbeat_at=timezone.now() - timedelta(minutes=2),
        )

        response = client.post(
            "/api/v2/chat/recover",
            data={
                "userId": create_test_token("user_stale", secret),
                "sessionId": "sess_stale",
                "messageId": "msg_stale",
            },
            format="json",
        )

        assert response.status_code == 200
        assert response.data["status"] == "stale"
        assert response.data["requestFound"] is True
        assert response.data["heartbeatTimeoutMs"] == 90000
        assert response.data["heartbeatAgeMs"] >= response.data["heartbeatTimeoutMs"]


@pytest.mark.django_db
class TestChatClientEventV2:
    """Tests for the client telemetry ingestion endpoint."""

    @pytest.fixture
    def client(self):
        return APIClient()

    @pytest.fixture
    def secret(self):
        return "test-secret-key-for-tokens"

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    @patch("chat.V2.views.capture_message")
    def test_valid_event_emits_telemetry(self, mock_capture_message, client, secret):
        response = client.post(
            "/api/v2/chat/client-event",
            data={
                "userId": create_test_token("user_event_ok", secret),
                "sessionId": "sess_event_ok",
                "messageId": "msg_event_ok",
                "timestamp": timezone.now().isoformat(),
                "event": "stream_missing_final_message",
                "context": {
                    "pageUrl": "https://www.sefaria.org/topics/shabbat?tab=sources",
                    "clientVersion": "1.0.0",
                    "locale": "en-US",
                },
            },
            format="json",
        )

        assert response.status_code == 200
        assert response.data == {"success": True}
        mock_capture_message.assert_called_once()
        assert mock_capture_message.call_args.kwargs["page_url"] == "/topics/shabbat"

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    def test_invalid_token_is_rejected(self, client):
        response = client.post(
            "/api/v2/chat/client-event",
            data={
                "userId": "not-a-valid-token",
                "sessionId": "sess_event_invalid_auth",
                "messageId": "msg_event_invalid_auth",
                "timestamp": timezone.now().isoformat(),
                "event": "stream_missing_final_message",
            },
            format="json",
        )

        assert response.status_code == 401
        assert response.data["error"] == "invalid_userId"

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    def test_missing_required_field_returns_validation_error(self, client, secret):
        response = client.post(
            "/api/v2/chat/client-event",
            data={
                "userId": create_test_token("user_event_invalid", secret),
                "sessionId": "sess_event_invalid",
                "messageId": "msg_event_invalid",
                "timestamp": timezone.now().isoformat(),
            },
            format="json",
        )

        assert response.status_code == 400
        assert response.data["error"] == "Invalid request"
