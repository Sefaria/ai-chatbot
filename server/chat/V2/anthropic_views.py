"""
Anthropic-compatible chat endpoint — used by Braintrust playground & evaluations.

This is a synchronous (non-streaming) endpoint that speaks the Anthropic Messages
API format, so Braintrust can treat our agent like a standard Claude model.

Request flow:
    POST /api/v2/chat/anthropic
    → authenticate via X-Api-Key header (encrypted user token)
    → create/resume session (X-Session-ID for multi-turn, otherwise ephemeral)
    → save user message to DB
    → run agent (ClaudeAgentService.send_message)
    → log response to DB
    → return Anthropic Messages API format response

Deviations from Anthropic Messages API standard:
- Response `metadata` field: We add trace_id, origin, and stats. Extra fields are ignored
  by standard clients.
- Response `id` format: Uses our message_id format (msg_ + 16 hex chars) rather than
  Anthropic's format (msg_ + 24 alphanumeric). Both use msg_ prefix, so this is compatible.
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
from ..serializers import AnthropicRequestSerializer
from .agent import AgentResponse, ConversationMessage, MessageContext, get_agent_service
from .logging import get_turn_logging_service
from .origin import DEFAULT_ORIGIN, resolve_origin
from .prompts.prompt_fragments import INTERNAL_ERROR_MESSAGE
from .sentry import capture_exception
from .services import create_or_get_session, load_session_summary, save_user_message
from .utils import flush_braintrust as _flush_braintrust

logger = logging.getLogger("chat")


def extract_user_message(messages: list[dict]) -> str:
    """
    Extract the last user message from Anthropic-format messages array.

    Handles both simple string content and content blocks format:
    - {"role": "user", "content": "Hello"}
    - {"role": "user", "content": [{"type": "text", "text": "Hello"}]}

    Returns empty string if no valid user message found.
    """
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue

        content = msg.get("content")
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, str):
                    text_parts.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text")
                    if isinstance(text, str):
                        text_parts.append(text)
            return "".join(text_parts)

    return ""


def to_anthropic_response(
    agent_response: AgentResponse,
    model: str,
    message_id: str,
    stats: dict,
    origin: str = DEFAULT_ORIGIN,
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
            "cache_read_input_tokens": stats.get("cacheReadTokens", 0),
            "cache_creation_input_tokens": stats.get("cacheCreationTokens", 0),
        },
        "metadata": {
            "trace_id": agent_response.trace_id,
            "origin": origin,
            "stats": stats,
        },
    }


def to_anthropic_error(
    error_type: str, message: str, latency_ms: int = 0, origin: str = ""
) -> dict:
    """Format an error response in Anthropic API style."""
    return {
        "error": {
            "type": error_type,
            "message": message,
        },
        "metadata": {
            "origin": origin,
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

    serializer = AnthropicRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            to_anthropic_error("invalid_request_error", str(serializer.errors)),
            status=status.HTTP_400_BAD_REQUEST,
        )
    data = serializer.validated_data

    # Authenticate via X-Api-Key header
    try:
        actor = authenticate_request(request)
    except (AuthenticationRequired, InvalidUserToken, UserTokenExpired) as exc:
        return Response(
            to_anthropic_error("authentication_error", str(exc)),
            status=status.HTTP_401_UNAUTHORIZED,
        )

    user_message_text = extract_user_message(data["messages"])
    if not user_message_text:
        return Response(
            to_anthropic_error("invalid_request_error", "No user message found"),
            status=status.HTTP_400_BAD_REQUEST,
        )

    metadata = data.get("metadata") or {}
    core_prompt_slug = metadata.get("core_prompt_slug") or settings.CORE_PROMPT_SLUG
    model = data.get("model") or settings.AGENT_MODEL

    # Note: streaming endpoint reads origin from request body context field (via serializer).
    caller_origin = (request.headers.get("X-Origin") or "")[:20]
    resolved_origin = resolve_origin(caller_origin)

    # Session handling: X-Session-ID header for multi-turn, or ephemeral
    session_id = request.headers.get("X-Session-ID")
    if session_id:
        # Multi-turn mode: use provided session, load summary
        session, _ = create_or_get_session(session_id, actor, current_flow=resolved_origin)
        summary_text = load_session_summary(session)
    else:
        # Stateless mode: ephemeral session, no summary
        session_id = f"ephemeral_{uuid.uuid4().hex[:16]}"
        session, _ = create_or_get_session(
            session_id, actor, validate_ownership=False, current_flow=resolved_origin
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
        flow=resolved_origin,
    )

    is_staff = request.headers.get("X-Is-Staff", "").lower() == "true"

    msg_context = MessageContext(
        summary_text=summary_text or None,
        session_id=session_id,
        origin=resolved_origin,
        is_staff=is_staff,
    )

    try:
        agent = get_agent_service()
        conversation = [ConversationMessage(role="user", content=user_message_text)]
        agent_response = asyncio.run(
            agent.send_message(
                messages=conversation,
                core_prompt_id=core_prompt_slug,
                on_progress=None,
                context=msg_context,
            )
        )
    except Exception as exc:
        latency_ms = int((time.time() - start_time) * 1000)
        logger.exception("Agent error in Anthropic endpoint")
        capture_exception(
            exc,
            endpoint="chat_anthropic_v2",
            session_id=session_id,
            turn_id=turn_id,
        )

        _flush_braintrust()

        # Log error message
        logging_service = get_turn_logging_service()
        error_msg = logging_service.record_error_message(
            session_id=session_id,
            actor=actor,
            turn_id=turn_id,
            latency_ms=latency_ms,
            error_text=INTERNAL_ERROR_MESSAGE,
        )
        db_user_message.response_message = error_msg
        db_user_message.save(update_fields=["response_message"])

        return Response(
            to_anthropic_error(
                "api_error", INTERNAL_ERROR_MESSAGE, latency_ms, origin=resolved_origin
            ),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    _flush_braintrust()

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
            origin=resolved_origin,
        )
    )
