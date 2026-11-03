"""
Authentication module for user token authentication.

Exports:
- Actor: Dataclass representing authenticated user identity
- authenticate_request: Main auth function
- anonymous_user_id: Persisted id for a logged-out visitor's anonId
- Exception classes for auth failures
"""

from .actor import Actor
from .anonymous import anonymous_user_id
from .auth_service import (
    AuthenticationError,
    AuthenticationRequired,
    InvalidUserToken,
    UserTokenExpired,
    authenticate_request,
)

__all__ = [
    "Actor",
    "anonymous_user_id",
    "authenticate_request",
    "AuthenticationError",
    "AuthenticationRequired",
    "InvalidUserToken",
    "UserTokenExpired",
]
