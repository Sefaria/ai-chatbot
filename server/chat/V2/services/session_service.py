"""
Session management service for chat operations.
"""

from django.db import IntegrityError, transaction
from django.utils import timezone

from ...auth import Actor
from ...models import ChatSession, ConversationSummary


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
    if session.user_id not in actor.user_id_candidates:
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
    defaults = {
        **actor.to_db_fields(),
        "last_activity": timezone.now(),
        **extra_defaults,
    }

    # The ownership check and the write happen under one row lock, so two callers racing on one
    # session id cannot both pass the check and the second take the session over.
    for attempt in range(2):
        try:
            with transaction.atomic():
                existing = (
                    ChatSession.objects.select_for_update().filter(session_id=session_id).first()
                )
                if existing is None:
                    return ChatSession.objects.create(session_id=session_id, **defaults), True
                if validate_ownership:
                    validate_session_ownership(existing, actor)
                for field, value in defaults.items():
                    setattr(existing, field, value)
                existing.save()
                return existing, False
        except IntegrityError:
            # Another request created the row between our read and our insert: lock it and check.
            if attempt:
                raise
    raise AssertionError("unreachable")


def load_session_summary(session: ChatSession) -> str:
    """
    Load the conversation summary for a session, if any.

    Args:
        session: The session to load summary for

    Returns:
        Summary text for prompting, or empty string if no summary exists
    """
    summary = ConversationSummary.objects.filter(session=session).first()
    return summary.to_prompt_text() if summary else ""
