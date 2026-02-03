"""
Authentication service for dual auth support (user tokens + API keys).

Routes authentication based on request format:
- Authorization: Bearer <key> -> API key auth
- Body userId field -> User token auth
"""

import logging

from django.conf import settings
from django.utils import timezone

from ..models import APIKey
from ..user_token_service import (
    UserTokenError,
    UserTokenExpiredError,
    decrypt_chatbot_user_token,
)
from .actor import Actor

logger = logging.getLogger("chat")


class AuthenticationError(Exception):
    """Base exception for authentication failures."""

    def __init__(self, message: str, error_code: str):
        super().__init__(message)
        self.error_code = error_code


class AuthenticationRequired(AuthenticationError):
    """Raised when no authentication credentials are provided."""

    def __init__(self):
        super().__init__("Authentication required", "auth_required")


class InvalidAPIKey(AuthenticationError):
    """Raised when an API key is invalid, expired, or inactive."""

    def __init__(self, reason: str = "invalid"):
        super().__init__(f"Invalid API key: {reason}", "invalid_api_key")


class InvalidUserToken(AuthenticationError):
    """Raised when a user token is invalid."""

    def __init__(self, reason: str = "invalid"):
        super().__init__(f"Invalid user token: {reason}", "invalid_user_token")


class UserTokenExpired(AuthenticationError):
    """Raised when a user token is expired."""

    def __init__(self):
        super().__init__("User token expired", "user_token_expired")


def authenticate_request(request, body_data: dict | None = None) -> Actor:
    """
    Authenticate a request and return an Actor.

    Authentication is attempted in order:
    1. Authorization header with Bearer token (API key)
    2. userId field in body_data (user token)

    Args:
        request: Django request object
        body_data: Optional dict containing userId field for user token auth

    Returns:
        Actor with either user_id or service_id set

    Raises:
        AuthenticationRequired: No credentials provided
        InvalidAPIKey: API key is invalid/expired/inactive
        InvalidUserToken: User token is invalid
        UserTokenExpired: User token is expired
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        api_key = auth_header[7:]
        return _authenticate_api_key(api_key)

    if body_data and body_data.get("userId"):
        return _authenticate_user_token(body_data["userId"])

    raise AuthenticationRequired()


def _authenticate_api_key(raw_key: str) -> Actor:
    """Validate an API key and return an Actor with service_id."""
    if not raw_key:
        raise InvalidAPIKey("empty key")

    key_hash = APIKey.hash_key(raw_key)

    try:
        api_key = APIKey.objects.get(key_hash=key_hash)
    except APIKey.DoesNotExist as exc:
        raise InvalidAPIKey("not found") from exc

    if not api_key.is_active:
        raise InvalidAPIKey("inactive")

    if api_key.expires_at and timezone.now() > api_key.expires_at:
        raise InvalidAPIKey("expired")

    api_key.last_used_at = timezone.now()
    api_key.save(update_fields=["last_used_at"])

    logger.debug(f"API key auth success: service_id={api_key.service_id}")
    return Actor(service_id=api_key.service_id)


def _authenticate_user_token(encrypted_token: str) -> Actor:
    """Decrypt a user token and return an Actor with user_id."""
    secret = settings.CHATBOT_USER_TOKEN_SECRET
    if not secret:
        logger.error("CHATBOT_USER_TOKEN_SECRET is not configured")
        raise InvalidUserToken("server configuration error")

    try:
        user_id = decrypt_chatbot_user_token(encrypted_token, secret)
    except UserTokenExpiredError as exc:
        raise UserTokenExpired() from exc
    except UserTokenError as exc:
        raise InvalidUserToken(str(exc)) from exc

    logger.debug(f"User token auth success: user_id={user_id}")
    return Actor(user_id=user_id)
