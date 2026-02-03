"""Tests for auth service."""

from datetime import timedelta

import pytest
from django.test import RequestFactory, override_settings
from django.utils import timezone

from chat.auth import (
    AuthenticationRequired,
    InvalidUserToken,
    UserTokenExpired,
    authenticate_request,
)
from chat.tests.test_streaming_integration import create_test_token


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
    def test_valid_user_token_in_body_returns_user_actor(self, factory, secret):
        """Test that valid user token in body returns actor with user_id."""
        token = create_test_token("user_12345", secret)
        request = factory.post("/test")

        actor = authenticate_request(request, {"userId": token})

        assert actor.user_id == "user_12345"

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    def test_valid_user_token_in_header_returns_user_actor(self, factory, secret):
        """Test that valid user token in X-User-Id header returns actor with user_id."""
        token = create_test_token("user_12345", secret)
        request = factory.post("/test", HTTP_X_USER_ID=token)

        actor = authenticate_request(request)

        assert actor.user_id == "user_12345"

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key")
    def test_header_takes_precedence_over_body(self, factory, secret):
        """Test that X-User-Id header takes precedence over body userId."""
        header_token = create_test_token("header_user", secret)
        body_token = create_test_token("body_user", secret)
        request = factory.post("/test", HTTP_X_USER_ID=header_token)

        actor = authenticate_request(request, {"userId": body_token})

        # Header should take precedence
        assert actor.user_id == "header_user"

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
class TestAuthenticationRequired:
    """Test authentication required scenarios."""

    @pytest.fixture
    def factory(self):
        return RequestFactory()

    def test_no_auth_raises_authentication_required(self, factory):
        """Test that missing auth raises AuthenticationRequired."""
        request = factory.post("/test")

        with pytest.raises(AuthenticationRequired):
            authenticate_request(request, {})

    def test_empty_body_raises_authentication_required(self, factory):
        """Test that empty body raises error."""
        request = factory.post("/test")

        with pytest.raises(AuthenticationRequired):
            authenticate_request(request, None)
