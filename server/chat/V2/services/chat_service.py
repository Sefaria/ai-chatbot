"""
Shared chat service operations used by both streaming and Anthropic endpoints.
"""

from ...auth import Actor
from ...models import ChatMessage, ChatSession


def save_user_message(
    session: ChatSession,
    actor: Actor,
    message_id: str,
    turn_id: str,
    content: str,
    **extra_fields,
) -> ChatMessage:
    """
    Save a user message to the database.

    Args:
        session: The session this message belongs to
        actor: The authenticated actor sending the message
        message_id: Unique message ID
        turn_id: Turn ID for grouping request/response
        content: Message content
        **extra_fields: Additional fields (client_timestamp, locale, etc.)

    Returns:
        The created ChatMessage
    """
    return ChatMessage.objects.create(
        message_id=message_id,
        session_id=session.session_id,
        turn_id=turn_id,
        role=ChatMessage.Role.USER,
        content=content,
        **actor.to_db_fields(),
        **extra_fields,
    )
