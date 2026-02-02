"""
Anthropic-compatible chat endpoint for Braintrust integration.

This endpoint accepts the Anthropic Messages API format and returns
responses in the same format, enabling use in Braintrust playground
and evaluations.
"""

import asyncio
import logging
import uuid

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .agent import AgentResponse, ConversationMessage
from .views import get_agent_service

logger = logging.getLogger("chat")


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


def to_anthropic_response(agent_response: AgentResponse, model: str) -> dict:
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
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": model,
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
        },
    }


@api_view(["POST"])
def chat_anthropic_v2(request):
    """
    Anthropic-compatible chat endpoint for Braintrust playground/evaluation.

    POST /api/v2/chat/anthropic

    Request (Anthropic Messages format):
        {
            "model": "sefaria-agent",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "What is Shabbat?"}]
        }

    Response (Anthropic Messages format):
        {
            "id": "msg_xxx",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "..."}],
            "model": "...",
            "stop_reason": "end_turn",
            "usage": {...}
        }
    """
    messages = request.data.get("messages", [])
    if not messages:
        return Response(
            {"error": {"type": "invalid_request_error", "message": "messages is required"}},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user_message = extract_user_message(messages)
    if not user_message:
        return Response(
            {"error": {"type": "invalid_request_error", "message": "No user message found"}},
            status=status.HTTP_400_BAD_REQUEST,
        )

    core_prompt_slug = (
        request.data.get("metadata", {}).get("core_prompt_slug", "") or settings.CORE_PROMPT_SLUG
    )
    model = request.data.get("model", "claude-sonnet-4-5-20250929")

    try:
        agent = get_agent_service()
        conversation = [ConversationMessage(role="user", content=user_message)]
        agent_response = asyncio.run(
            agent.send_message(
                messages=conversation,
                core_prompt_id=core_prompt_slug,
                on_progress=None,
                summary_text="",
            )
        )
    except Exception as e:
        logger.exception("Agent error in Anthropic endpoint")
        return Response(
            {"error": {"type": "api_error", "message": str(e)}},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return Response(to_anthropic_response(agent_response, model))
