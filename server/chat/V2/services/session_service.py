"""
Session management service for chat operations.
"""

from django.utils import timezone

from ...auth import Actor
from ...models import ChatSession


class SessionOwnershipError(Exception):
    """Raised when an actor tries to access a session they don't own."""


def validate_session_ownership(session: ChatSession, actor: Actor) -> None:
    """
    Validate that an actor owns a session.

    Args:
        session: The session to check ownership of
        actor: The actor attempting to access the session

    Raises:
        SessionOwnershipError: If the actor doesn't own the session
    """
    if session.user_id != actor.user_id:
        raise SessionOwnershipError("Session belongs to different user")


def create_or_get_session(
    session_id: str,
    actor: Actor,
    validate_ownership: bool = True,
    **extra_defaults,
) -> tuple[ChatSession, bool]:
    """
    Create or retrieve a session, optionally validating ownership.

    Args:
        session_id: The session ID to create or retrieve
        actor: The actor owning the session
        validate_ownership: Whether to validate ownership on existing sessions
        **extra_defaults: Additional fields to set on creation/update

    Returns:
        Tuple of (session, created) where created is True if a new session was created

    Raises:
        SessionOwnershipError: If validate_ownership is True and session belongs to different actor
    """
    # Check ownership BEFORE updating to prevent overwriting another user's session
    if validate_ownership:
        existing = ChatSession.objects.filter(session_id=session_id).first()
        if existing:
            validate_session_ownership(existing, actor)

    defaults = {
        **actor.to_db_fields(),
        "last_activity": timezone.now(),
        **extra_defaults,
    }

    session, created = ChatSession.objects.update_or_create(
        session_id=session_id,
        defaults=defaults,
    )

    return session, created
