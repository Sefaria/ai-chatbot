"""Tests for history endpoint with user token authentication."""

from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from chat.models import ChatMessage, ChatSession
from chat.tests.test_streaming_integration import create_test_token


@pytest.mark.django_db
class TestHistoryEndpointAuthentication:
    """Test authentication for history endpoint."""

    @pytest.fixture
    def client(self):
        return APIClient()

    @pytest.fixture
    def secret(self):
        return "test-secret-key"

    def test_missing_session_id_returns_400(self, client):
        """Missing sessionId should return 400."""
        response = client.get("/api/history")
        assert response.status_code == 400
        assert "sessionId" in response.data["error"]

    def test_missing_auth_returns_401(self, client):
        """Missing authentication should return 401."""
        response = client.get("/api/history?sessionId=test-session")
        assert response.status_code == 401

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    def test_valid_user_token_authenticates(self, client, secret):
        """Valid user token should authenticate successfully."""
        token = create_test_token("user_123", secret)
        response = client.get(f"/api/history?sessionId=test-session&userId={token}")
        assert response.status_code == 200

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    def test_expired_user_token_returns_401(self, client, secret):
        """Expired user token should return 401."""
        token = create_test_token(
            "user_123", secret, expires_at=(timezone.now() - timedelta(hours=1))
        )
        response = client.get(f"/api/history?sessionId=test-session&userId={token}")
        assert response.status_code == 401
        assert response.data["error"] == "userId_expired"


@pytest.mark.django_db
class TestHistoryEndpointUserMessages:
    """Test history endpoint returns correct messages for users."""

    @pytest.fixture
    def client(self):
        return APIClient()

    @pytest.fixture
    def secret(self):
        return "test-secret-key"

    @pytest.fixture
    def user_session(self):
        """Create a session with user messages."""
        session = ChatSession.objects.create(
            session_id="user-session-123",
            user_id="user_abc",
        )
        # Create some messages
        ChatMessage.objects.create(
            message_id="msg_1",
            session_id=session.session_id,
            user_id="user_abc",
            role=ChatMessage.Role.USER,
            content="Hello",
        )
        ChatMessage.objects.create(
            message_id="msg_2",
            session_id=session.session_id,
            user_id="user_abc",
            role=ChatMessage.Role.ASSISTANT,
            content="Hi there!",
        )
        return session

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    def test_user_can_fetch_own_messages(self, client, secret, user_session):
        """User can fetch their own session messages."""
        token = create_test_token("user_abc", secret)
        response = client.get(f"/api/history?sessionId={user_session.session_id}&userId={token}")

        assert response.status_code == 200
        assert len(response.data["messages"]) == 2
        assert response.data["messages"][0]["content"] == "Hello"
        assert response.data["messages"][1]["content"] == "Hi there!"

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    def test_user_cannot_fetch_other_user_messages(self, client, secret, user_session):
        """User cannot fetch another user's messages."""
        # Create messages for different user
        ChatMessage.objects.create(
            message_id="msg_other",
            session_id=user_session.session_id,
            user_id="other_user",
            role=ChatMessage.Role.USER,
            content="Other user's message",
        )

        # Try to fetch as user_abc
        token = create_test_token("user_abc", secret)
        response = client.get(f"/api/history?sessionId={user_session.session_id}&userId={token}")

        assert response.status_code == 200
        # Should only see user_abc's messages, not other_user's
        assert len(response.data["messages"]) == 2
        for msg in response.data["messages"]:
            assert "Other user's message" not in msg["content"]

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    def test_user_gets_session_info(self, client, secret, user_session):
        """User gets session info for their own sessions."""
        user_session.turn_count = 5
        user_session.total_input_tokens = 100
        user_session.total_output_tokens = 200
        user_session.save()

        token = create_test_token("user_abc", secret)
        response = client.get(f"/api/history?sessionId={user_session.session_id}&userId={token}")

        assert response.status_code == 200
        assert response.data["session"] is not None
        assert response.data["session"]["turnCount"] == 5
        assert response.data["session"]["totalTokens"] == 300


@pytest.mark.django_db
class TestHistoryEndpointPagination:
    """Test history endpoint pagination."""

    @pytest.fixture
    def client(self):
        return APIClient()

    @pytest.fixture
    def secret(self):
        return "test-secret-key"

    @pytest.fixture
    def session_with_many_messages(self):
        """Create a session with many messages."""
        session = ChatSession.objects.create(
            session_id="pagination-session",
            user_id="pagination-user",
        )
        # Create 25 messages
        for i in range(25):
            ChatMessage.objects.create(
                message_id=f"page_msg_{i}",
                session_id=session.session_id,
                user_id="pagination-user",
                role=ChatMessage.Role.USER,
                content=f"Message {i}",
            )
        return session

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    def test_default_limit_is_20(self, client, secret, session_with_many_messages):
        """Default limit should be 20 messages."""
        token = create_test_token("pagination-user", secret)
        response = client.get(
            f"/api/history?sessionId={session_with_many_messages.session_id}&userId={token}"
        )

        assert response.status_code == 200
        assert len(response.data["messages"]) == 20
        assert response.data["hasMore"] is True

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    def test_custom_limit(self, client, secret, session_with_many_messages):
        """Custom limit should work."""
        token = create_test_token("pagination-user", secret)
        response = client.get(
            f"/api/history?sessionId={session_with_many_messages.session_id}&userId={token}&limit=5"
        )

        assert response.status_code == 200
        assert len(response.data["messages"]) == 5
        assert response.data["hasMore"] is True

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    def test_max_limit_is_100(self, client, secret, session_with_many_messages):
        """Limit should be capped at 100."""
        token = create_test_token("pagination-user", secret)
        response = client.get(
            f"/api/history?sessionId={session_with_many_messages.session_id}&userId={token}&limit=500"
        )

        assert response.status_code == 200
        # Should return all 25 messages (capped at 100, but only 25 exist)
        assert len(response.data["messages"]) == 25
        assert response.data["hasMore"] is False
