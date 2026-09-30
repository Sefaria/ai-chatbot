"""
Authentication service for user token authentication.
"""

import hashlib
import logging
import re

from django.conf import settings

from ..user_token_service import (
    UserTokenError,
    UserTokenExpiredError,
    decrypt_chatbot_user_identity,
)
from .actor import Actor

logger = logging.getLogger("chat")

ANON_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,100}$")
ANON_USER_ID_PREFIX = "anon:"


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


def authenticate_request(
    request, body_data: dict | None = None, *, allow_anonymous: bool = False
) -> Actor:
    """
    Authenticate a request and return an Actor.

    Authentication is attempted in order:
    1. X-Api-Key header (for Anthropic-compatible endpoints)
    2. userId field in body_data (for streaming endpoint)
    3. anonId field in body_data, when allow_anonymous (logged-out visitors)

    Args:
        request: Django request object
        body_data: Optional dict containing userId field for user token auth
        allow_anonymous: Accept a client-generated anonId when no userId is sent

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

    anon_id = (body_data or {}).get("anonId") or ""
    if allow_anonymous and ANON_ID_PATTERN.match(anon_id):
        return Actor(user_id=anonymous_user_id(anon_id), is_anonymous=True)

    raise AuthenticationRequired()


def anonymous_user_id(anon_id: str) -> str:
    """Persisted id for a logged-out visitor; hashed like signed-in ids."""
    return ANON_USER_ID_PREFIX + hashlib.sha256(anon_id.encode()).hexdigest()


def _authenticate_user_token(encrypted_token: str) -> Actor:
    """Decrypt a user token and return an Actor with user_id."""
    secret = settings.CHATBOT_USER_TOKEN_SECRET
    if not secret:
        logger.error("CHATBOT_USER_TOKEN_SECRET is not configured")
        raise InvalidUserToken("server configuration error")

    try:
        identity = decrypt_chatbot_user_identity(encrypted_token, secret)
    except UserTokenExpiredError as exc:
        raise UserTokenExpired() from exc
    except UserTokenError as exc:
        raise InvalidUserToken(str(exc)) from exc

    return Actor(
        user_id=identity.user_id,
        encrypted_token=encrypted_token,
        sefaria_user_id=identity.sefaria_user_id,
    )
