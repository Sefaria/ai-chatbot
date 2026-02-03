"""Tests for auth service."""

from datetime import timedelta

import pytest
from django.test import RequestFactory, override_settings
from django.utils import timezone

from chat.auth import (
    AuthenticationRequired,
    InvalidAPIKey,
    InvalidUserToken,
    UserTokenExpired,
    authenticate_request,
)
from chat.models import APIKey
from chat.tests.test_streaming_integration import create_test_token


@pytest.mark.django_db
class TestAPIKeyAuthentication:
    """Test API key authentication."""

    @pytest.fixture
    def factory(self):
        return RequestFactory()

    @pytest.fixture
    def api_key(self):
        """Create a test API key."""
        raw_key = "sk_live_test_key_12345"
        key_hash = APIKey.hash_key(raw_key)
        api_key = APIKey.objects.create(
            key_hash=key_hash,
            service_id="test-service",
            name="Test Service",
            is_active=True,
        )
        return raw_key, api_key

    def test_valid_api_key_returns_service_actor(self, factory, api_key):
        """Test that valid API key returns actor with service_id."""
        raw_key, _ = api_key
        request = factory.post("/test", HTTP_AUTHORIZATION=f"Bearer {raw_key}")

        actor = authenticate_request(request, {})

        assert actor.is_service
        assert actor.service_id == "test-service"
        assert actor.user_id is None

    def test_invalid_api_key_raises_error(self, factory):
        """Test that invalid API key raises InvalidAPIKey."""
        request = factory.post("/test", HTTP_AUTHORIZATION="Bearer invalid_key")

        with pytest.raises(InvalidAPIKey, match="not found"):
            authenticate_request(request, {})

    def test_inactive_api_key_raises_error(self, factory, api_key):
        """Test that inactive API key raises InvalidAPIKey."""
        raw_key, key_obj = api_key
        key_obj.is_active = False
        key_obj.save()

        request = factory.post("/test", HTTP_AUTHORIZATION=f"Bearer {raw_key}")

        with pytest.raises(InvalidAPIKey, match="inactive"):
            authenticate_request(request, {})

    def test_expired_api_key_raises_error(self, factory, api_key):
        """Test that expired API key raises InvalidAPIKey."""
        raw_key, key_obj = api_key
        key_obj.expires_at = timezone.now() - timedelta(hours=1)
        key_obj.save()

        request = factory.post("/test", HTTP_AUTHORIZATION=f"Bearer {raw_key}")

        with pytest.raises(InvalidAPIKey, match="expired"):
            authenticate_request(request, {})

    def test_api_key_updates_last_used(self, factory, api_key):
        """Test that using API key updates last_used_at."""
        raw_key, key_obj = api_key
        assert key_obj.last_used_at is None

        request = factory.post("/test", HTTP_AUTHORIZATION=f"Bearer {raw_key}")
        authenticate_request(request, {})

        key_obj.refresh_from_db()
        assert key_obj.last_used_at is not None

    def test_empty_bearer_token_raises_error(self, factory):
        """Test that empty bearer token raises error."""
        request = factory.post("/test", HTTP_AUTHORIZATION="Bearer ")

        with pytest.raises(InvalidAPIKey, match="empty"):
            authenticate_request(request, {})


@pytest.mark.django_db
class TestUserTokenAuthentication:
    """Test user token authentication."""

    @pytest.fixture
    def factory(self):
        return RequestFactory()

    @pytest.fixture
    def secret(self):
        return "test-secret-key"

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    def test_valid_user_token_returns_user_actor(self, factory, secret):
        """Test that valid user token returns actor with user_id."""
        token = create_test_token("user_12345", secret)
        request = factory.post("/test")

        actor = authenticate_request(request, {"userId": token})

        assert actor.is_user
        assert actor.user_id == "user_12345"
        assert actor.service_id is None

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    def test_expired_user_token_raises_error(self, factory, secret):
        """Test that expired user token raises UserTokenExpired."""
        token = create_test_token(
            "user_12345", secret, expires_at=(timezone.now() - timedelta(hours=1))
        )
        request = factory.post("/test")

        with pytest.raises(UserTokenExpired):
            authenticate_request(request, {"userId": token})

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    def test_invalid_user_token_raises_error(self, factory):
        """Test that invalid user token raises InvalidUserToken."""
        request = factory.post("/test")

        with pytest.raises(InvalidUserToken):
            authenticate_request(request, {"userId": "invalid_token"})

    @override_settings(CHATBOT_USER_TOKEN_SECRET="wrong-secret")
    def test_wrong_secret_raises_error(self, factory, secret):
        """Test that token with wrong secret raises error."""
        token = create_test_token("user_12345", secret)
        request = factory.post("/test")

        with pytest.raises(InvalidUserToken):
            authenticate_request(request, {"userId": token})


@pytest.mark.django_db
class TestAuthenticationRouting:
    """Test authentication routing between API key and user token."""

    @pytest.fixture
    def factory(self):
        return RequestFactory()

    @pytest.fixture
    def api_key(self):
        """Create a test API key."""
        raw_key = "sk_live_routing_test"
        key_hash = APIKey.hash_key(raw_key)
        APIKey.objects.create(
            key_hash=key_hash,
            service_id="routing-test",
            name="Routing Test",
            is_active=True,
        )
        return raw_key

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret")
    def test_api_key_takes_precedence_over_user_token(self, factory, api_key):
        """Test that Authorization header takes precedence over userId."""
        user_token = create_test_token("user_123", "test-secret")
        request = factory.post(
            "/test",
            HTTP_AUTHORIZATION=f"Bearer {api_key}",
        )

        actor = authenticate_request(request, {"userId": user_token})

        # Should use API key, not user token
        assert actor.is_service
        assert actor.service_id == "routing-test"

    def test_no_auth_raises_authentication_required(self, factory):
        """Test that missing auth raises AuthenticationRequired."""
        request = factory.post("/test")

        with pytest.raises(AuthenticationRequired):
            authenticate_request(request, {})

    def test_empty_body_raises_authentication_required(self, factory):
        """Test that empty body without header raises error."""
        request = factory.post("/test")

        with pytest.raises(AuthenticationRequired):
            authenticate_request(request, None)
