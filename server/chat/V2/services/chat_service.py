"""
Shared chat service operations used by both streaming and Anthropic endpoints.
"""

from collections.abc import Callable

from ...auth import Actor
from ...models import ChatMessage, ChatSession
from ..agent import AgentResponse, ConversationMessage, get_agent_service


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


async def run_agent_turn(
    user_content: str,
    core_prompt_slug: str,
    summary_text: str = "",
    on_progress: Callable | None = None,
) -> AgentResponse:
    """
    Run a single agent turn with the given message.

    Args:
        user_content: The user's message content
        core_prompt_slug: Braintrust prompt slug for agent instructions
        summary_text: Optional conversation summary for context
        on_progress: Optional callback for streaming progress updates

    Returns:
        AgentResponse with content, tool_calls, and trace_id
    """
    agent = get_agent_service()
    conversation = [ConversationMessage(role="user", content=user_content)]

    return await agent.send_message(
        messages=conversation,
        core_prompt_id=core_prompt_slug,
        on_progress=on_progress,
        summary_text=summary_text,
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
