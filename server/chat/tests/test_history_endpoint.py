"""Tests for history endpoint with dual authentication."""

from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from chat.models import APIKey, ChatMessage, ChatSession
from chat.tests.test_streaming_integration import create_test_token

# Test API key value
TEST_API_KEY = "sk_live_history_test_key"


@pytest.mark.django_db
class TestHistoryEndpointAuthentication:
    """Test authentication for history endpoint."""

    @pytest.fixture
    def client(self):
        return APIClient()

    @pytest.fixture
    def api_key(self):
        """Create a test API key."""
        key_hash = APIKey.hash_key(TEST_API_KEY)
        return APIKey.objects.create(
            key_hash=key_hash,
            service_id="history-test-service",
            name="History Test Key",
            is_active=True,
        )

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

    def test_valid_api_key_authenticates(self, client, api_key):
        """Valid API key should authenticate successfully."""
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {TEST_API_KEY}")
        response = client.get("/api/history?sessionId=test-session")
        assert response.status_code == 200

    def test_invalid_api_key_returns_401(self, client):
        """Invalid API key should return 401."""
        client.credentials(HTTP_AUTHORIZATION="Bearer invalid_key")
        response = client.get("/api/history?sessionId=test-session")
        assert response.status_code == 401
        assert response.data["error"] == "invalid_api_key"

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


@pytest.mark.django_db
class TestHistoryEndpointServiceMessages:
    """Test history endpoint returns correct messages for services."""

    @pytest.fixture
    def client(self):
        return APIClient()

    @pytest.fixture
    def api_key(self):
        """Create a test API key."""
        key_hash = APIKey.hash_key(TEST_API_KEY)
        return APIKey.objects.create(
            key_hash=key_hash,
            service_id="test-service",
            name="Test Service",
            is_active=True,
        )

    @pytest.fixture
    def service_session(self, api_key):
        """Create a session with service messages."""
        session = ChatSession.objects.create(
            session_id="service-session-123",
            service_id="test-service",
        )
        # Create some messages
        ChatMessage.objects.create(
            message_id="svc_msg_1",
            session_id=session.session_id,
            service_id="test-service",
            role=ChatMessage.Role.USER,
            content="Service question",
        )
        ChatMessage.objects.create(
            message_id="svc_msg_2",
            session_id=session.session_id,
            service_id="test-service",
            role=ChatMessage.Role.ASSISTANT,
            content="Service answer",
        )
        return session

    def test_service_can_fetch_own_messages(self, client, api_key, service_session):
        """Service can fetch its own session messages."""
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {TEST_API_KEY}")
        response = client.get(f"/api/history?sessionId={service_session.session_id}")

        assert response.status_code == 200
        assert len(response.data["messages"]) == 2
        assert response.data["messages"][0]["content"] == "Service question"
        assert response.data["messages"][1]["content"] == "Service answer"

    def test_service_cannot_fetch_other_service_messages(self, client, api_key, service_session):
        """Service cannot fetch another service's messages."""
        # Create messages for different service
        ChatMessage.objects.create(
            message_id="other_svc_msg",
            session_id=service_session.session_id,
            service_id="other-service",
            role=ChatMessage.Role.USER,
            content="Other service's message",
        )

        # Try to fetch as test-service
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {TEST_API_KEY}")
        response = client.get(f"/api/history?sessionId={service_session.session_id}")

        assert response.status_code == 200
        # Should only see test-service's messages
        assert len(response.data["messages"]) == 2
        for msg in response.data["messages"]:
            assert "Other service's message" not in msg["content"]

    def test_service_gets_session_info(self, client, api_key, service_session):
        """Service gets session info for its own sessions."""
        service_session.turn_count = 5
        service_session.total_input_tokens = 100
        service_session.total_output_tokens = 200
        service_session.save()

        client.credentials(HTTP_AUTHORIZATION=f"Bearer {TEST_API_KEY}")
        response = client.get(f"/api/history?sessionId={service_session.session_id}")

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
    def api_key(self):
        """Create a test API key."""
        key_hash = APIKey.hash_key(TEST_API_KEY)
        return APIKey.objects.create(
            key_hash=key_hash,
            service_id="pagination-service",
            name="Pagination Test",
            is_active=True,
        )

    @pytest.fixture
    def session_with_many_messages(self, api_key):
        """Create a session with many messages."""
        session = ChatSession.objects.create(
            session_id="pagination-session",
            service_id="pagination-service",
        )
        # Create 25 messages
        for i in range(25):
            ChatMessage.objects.create(
                message_id=f"page_msg_{i}",
                session_id=session.session_id,
                service_id="pagination-service",
                role=ChatMessage.Role.USER,
                content=f"Message {i}",
            )
        return session

    def test_default_limit_is_20(self, client, api_key, session_with_many_messages):
        """Default limit should be 20 messages."""
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {TEST_API_KEY}")
        response = client.get(f"/api/history?sessionId={session_with_many_messages.session_id}")

        assert response.status_code == 200
        assert len(response.data["messages"]) == 20
        assert response.data["hasMore"] is True

    def test_custom_limit(self, client, api_key, session_with_many_messages):
        """Custom limit should work."""
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {TEST_API_KEY}")
        response = client.get(
            f"/api/history?sessionId={session_with_many_messages.session_id}&limit=5"
        )

        assert response.status_code == 200
        assert len(response.data["messages"]) == 5
        assert response.data["hasMore"] is True

    def test_max_limit_is_100(self, client, api_key, session_with_many_messages):
        """Limit should be capped at 100."""
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {TEST_API_KEY}")
        response = client.get(
            f"/api/history?sessionId={session_with_many_messages.session_id}&limit=500"
        )

        assert response.status_code == 200
        # Should return all 25 messages (capped at 100, but only 25 exist)
        assert len(response.data["messages"]) == 25
        assert response.data["hasMore"] is False
