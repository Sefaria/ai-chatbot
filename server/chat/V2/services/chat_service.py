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


def apply_page_context_to_message(message: str, page_url: str) -> str:
    """
    Append page URL context to a user message for the agent prompt.

    Args:
        message: The original user message
        page_url: The URL of the page the user is viewing

    Returns:
        Message with page context appended, or original message if no URL
    """
    if not page_url:
        return message

    return (
        f"{message}\n\n"
        f"User is currently on the Sefaria url: {page_url}. "
        "If the context is relevant, use that information in your response"
    )
