"""
Authentication service for user token authentication.
"""

import logging

from django.conf import settings

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
    1. X-Api-Key header (for Anthropic-compatible endpoints)
    2. userId field in body_data (for streaming endpoint)

    Args:
        request: Django request object
        body_data: Optional dict containing userId field for user token auth

    Returns:
        Actor with user_id set

    Raises:
        AuthenticationRequired: No credentials provided
        InvalidUserToken: User token is invalid
        UserTokenExpired: User token is expired
    """
    # Check X-Api-Key header first (for Anthropic-compatible endpoints)
    api_key_header = request.headers.get("X-Api-Key")
    if api_key_header:
        return _authenticate_user_token(api_key_header)

    # Fall back to userId in body (for streaming endpoint)
    if body_data and body_data.get("userId"):
        return _authenticate_user_token(body_data["userId"])

    raise AuthenticationRequired()


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

    return Actor(user_id=user_id)
