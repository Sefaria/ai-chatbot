"""
Authentication module for dual auth support.

Exports:
- Actor: Dataclass representing authenticated identity
- authenticate_request: Main auth function
- Exception classes for auth failures
"""

from .actor import Actor
from .auth_service import (
    AuthenticationError,
    AuthenticationRequired,
    InvalidAPIKey,
    InvalidUserToken,
    UserTokenExpired,
    authenticate_request,
)

__all__ = [
    "Actor",
    "authenticate_request",
    "AuthenticationError",
    "AuthenticationRequired",
    "InvalidAPIKey",
    "InvalidUserToken",
    "UserTokenExpired",
]
