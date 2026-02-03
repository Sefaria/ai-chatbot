"""
Authentication module for user token authentication.

Exports:
- Actor: Dataclass representing authenticated user identity
- authenticate_request: Main auth function
- Exception classes for auth failures
"""

from .actor import Actor
from .auth_service import (
    AuthenticationError,
    AuthenticationRequired,
    InvalidUserToken,
    UserTokenExpired,
    authenticate_request,
)

__all__ = [
    "Actor",
    "authenticate_request",
    "AuthenticationError",
    "AuthenticationRequired",
    "InvalidUserToken",
    "UserTokenExpired",
]
