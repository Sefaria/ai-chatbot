"""
Anthropic-compatible chat endpoint for Braintrust integration.

This endpoint accepts the Anthropic Messages API format and returns
responses in the same format, enabling use in Braintrust playground
and evaluations. Includes full logging and metrics parity with the
streaming endpoint.
"""

import asyncio
import logging
import time
import uuid

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from ..auth import (
    AuthenticationRequired,
    InvalidUserToken,
    UserTokenExpired,
    authenticate_request,
)
from ..models import ChatMessage
from .agent import AgentResponse, ConversationMessage, get_agent_service
from .logging import get_turn_logging_service
from .services import create_or_get_session, load_session_summary, save_user_message

logger = logging.getLogger("chat")

# Origin identifier for Braintrust requests (used in metadata)
BRAINTRUST_ORIGIN = "braintrust"


def extract_user_message(messages: list[dict]) -> str:
    """
    Extract the last user message from Anthropic-format messages array.

    Handles both simple string content and content blocks format:
    - {"role": "user", "content": "Hello"}
    - {"role": "user", "content": [{"type": "text", "text": "Hello"}]}
    """
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue

        content = msg.get("content")
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            text_parts = [
                block.get("text", "")
                if isinstance(block, dict) and block.get("type") == "text"
                else block
                if isinstance(block, str)
                else ""
                for block in content
            ]
            return "".join(text_parts)

    return ""


def to_anthropic_response(
    agent_response: AgentResponse, model: str, message_id: str, stats: dict
) -> dict:
    """
    Transform our AgentResponse to Anthropic Messages API format.

    Maps:
    - agent_response.content -> content[0] as text block
    - agent_response.tool_calls -> content[1:] as tool_use blocks
    """
    content_blocks = []

    if agent_response.content:
        content_blocks.append(
            {
                "type": "text",
                "text": agent_response.content,
            }
        )

    for tc in agent_response.tool_calls:
        content_blocks.append(
            {
                "type": "tool_use",
                "id": f"toolu_{uuid.uuid4().hex[:24]}",
                "name": tc.get("tool_name", "unknown"),
                "input": tc.get("tool_input", {}),
            }
        )

    return {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": model,
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": stats.get("inputTokens", 0),
            "output_tokens": stats.get("outputTokens", 0),
        },
        "metadata": {
            "trace_id": agent_response.trace_id,
            "origin": BRAINTRUST_ORIGIN,
            "stats": stats,
        },
    }


def to_anthropic_error(error_type: str, message: str, latency_ms: int = 0) -> dict:
    """Format an error response in Anthropic API style."""
    return {
        "error": {
            "type": error_type,
            "message": message,
        },
        "metadata": {
            "origin": BRAINTRUST_ORIGIN,
            "latency_ms": latency_ms,
        },
    }


@api_view(["POST"])
def chat_anthropic_v2(request):
    """
    Anthropic-compatible chat endpoint for Braintrust playground/evaluation.

    POST /api/v2/chat/anthropic

    Headers:
        X-Api-Key: <encrypted_user_token> (required)
        X-Session-ID: <session_id> (optional, for multi-turn)

    Request (Anthropic Messages format):
        {
            "model": "sefaria-agent",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "What is Shabbat?"}],
            "metadata": {
                "core_prompt_slug": "optional-prompt-slug"
            }
        }

    Response (Anthropic Messages format):
        {
            "id": "msg_xxx",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "..."}],
            "model": "...",
            "stop_reason": "end_turn",
            "usage": {...},
            "metadata": {"trace_id": "...", "origin": "braintrust", "stats": {...}}
        }
    """
    start_time = time.time()

    # Authenticate via X-User-Id header
    try:
        actor = authenticate_request(request)
    except (AuthenticationRequired, InvalidUserToken, UserTokenExpired) as exc:
        return Response(
            to_anthropic_error("authentication_error", str(exc)),
            status=status.HTTP_401_UNAUTHORIZED,
        )

    messages = request.data.get("messages", [])
    if not messages:
        return Response(
            to_anthropic_error("invalid_request_error", "messages is required"),
            status=status.HTTP_400_BAD_REQUEST,
        )

    user_message_text = extract_user_message(messages)
    if not user_message_text:
        return Response(
            to_anthropic_error("invalid_request_error", "No user message found"),
            status=status.HTTP_400_BAD_REQUEST,
        )

    metadata = request.data.get("metadata", {})
    core_prompt_slug = metadata.get("core_prompt_slug", "") or settings.CORE_PROMPT_SLUG
    model = request.data.get("model", "claude-sonnet-4-5-20250929")

    # Session handling: X-Session-ID header for multi-turn, or ephemeral
    session_id = request.headers.get("X-Session-ID")
    if session_id:
        # Multi-turn mode: use provided session, load summary
        session, _ = create_or_get_session(session_id, actor, current_flow=BRAINTRUST_ORIGIN)
        summary_text = load_session_summary(session)
    else:
        # Stateless mode: ephemeral session, no summary
        session_id = f"ephemeral_{uuid.uuid4().hex[:16]}"
        session, _ = create_or_get_session(
            session_id, actor, validate_ownership=False, current_flow=BRAINTRUST_ORIGIN
        )
        summary_text = ""

    turn_id = ChatMessage.generate_turn_id()
    user_message_id = ChatMessage.generate_message_id()

    # Save user message to DB
    db_user_message = save_user_message(
        session=session,
        actor=actor,
        message_id=user_message_id,
        turn_id=turn_id,
        content=user_message_text,
        flow=BRAINTRUST_ORIGIN,
    )

    try:
        agent = get_agent_service()
        conversation = [ConversationMessage(role="user", content=user_message_text)]
        agent_response = asyncio.run(
            agent.send_message(
                messages=conversation,
                core_prompt_id=core_prompt_slug,
                on_progress=None,
                summary_text=summary_text,
            )
        )
    except Exception:
        latency_ms = int((time.time() - start_time) * 1000)
        logger.exception("Agent error in Anthropic endpoint")

        # Log error message
        logging_service = get_turn_logging_service()
        error_msg = logging_service.record_error_message(
            session_id=session_id,
            actor=actor,
            turn_id=turn_id,
            latency_ms=latency_ms,
            error_text="Internal server error",
        )
        db_user_message.response_message = error_msg
        db_user_message.save(update_fields=["response_message"])

        return Response(
            to_anthropic_error("api_error", "Internal server error", latency_ms),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    latency_ms = int((time.time() - start_time) * 1000)

    # Log successful response
    logging_service = get_turn_logging_service()
    logging_result = logging_service.finalize_success(
        session=session,
        user_message=db_user_message,
        agent_response=agent_response,
        latency_ms=latency_ms,
        model_name=model,
        summary_text=summary_text,
    )

    response_message = logging_result.response_message

    return Response(
        to_anthropic_response(
            agent_response=agent_response,
            model=model,
            message_id=response_message.message_id,
            stats=logging_result.stats,
        )
    )
