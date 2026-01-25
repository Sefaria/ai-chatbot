"""
Chat API views with routed Claude agent integration.

Thin view layer that delegates to the orchestrator for business logic.
Each view handles: request validation, response formatting, HTTP mechanics.
"""

import asyncio
import contextvars
import json
import logging
import queue
import time
import uuid
from datetime import datetime
from threading import Thread

from django.conf import settings
from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .agent import AgentProgressUpdate, ConversationMessage
from .models import ChatMessage, ChatSession
from .orchestrator import (
    RefusalResult,
    SessionInfo,
    TurnLimitReached,
    check_turn_limit,
    complete_turn,
    complete_turn_with_error,
    complete_turn_with_refusal,
    execute_agent,
    get_agent_service,
    get_router,
    load_conversation_from_db,
    prepare_turn,
    turn_span_context,
)
from .prompts import get_prompt_service
from .router import Flow
from .serializers import (
    ChatRequestSerializer,
    HistoryMessageSerializer,
    OpenAIChatRequestSerializer,
)
from .test_questions import build_test_response_data, get_test_response

logger = logging.getLogger("chat")


# ---------------------------------------------------------------------------
# Response Formatters
# ---------------------------------------------------------------------------


def format_standard_response(result, session_id: str) -> dict:
    """Format response for standard chat endpoint."""
    return {
        "messageId": result.response_message.message_id,
        "sessionId": session_id,
        "timestamp": result.response_message.server_timestamp.isoformat(),
        "markdown": result.agent_response.content,
        "routing": {
            "flow": result.route_result.flow.value,
            "decisionId": result.route_result.decision_id,
            "confidence": result.route_result.confidence,
            "wasRefused": result.agent_response.was_refused,
        },
        "session": SessionInfo.from_session(result.session).to_dict(),
    }


def format_error_response(
    error_message: ChatMessage, route_result, session: ChatSession, session_id: str
) -> dict:
    """Format error response for standard chat endpoint."""
    return {
        "messageId": error_message.message_id,
        "sessionId": session_id,
        "timestamp": error_message.server_timestamp.isoformat(),
        "markdown": error_message.content,
        "routing": {
            "flow": route_result.flow.value,
            "decisionId": route_result.decision_id,
            "wasRefused": False,
        },
        "session": SessionInfo.from_session(session).to_dict(),
    }


def format_openai_response(result, model: str) -> dict:
    """Format response for OpenAI-compatible endpoint."""
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": result.agent_response.content,
                },
                "finish_reason": "content_filter" if result.agent_response.was_refused else "stop",
            }
        ],
        "usage": {
            "prompt_tokens": result.agent_response.input_tokens,
            "completion_tokens": result.agent_response.output_tokens,
            "total_tokens": result.agent_response.input_tokens
            + result.agent_response.output_tokens,
        },
        "routing": {
            "flow": result.route_result.flow.value,
            "decision_id": result.route_result.decision_id,
            "confidence": result.route_result.confidence,
            "was_refused": result.agent_response.was_refused,
        },
        "session": SessionInfo.from_session(result.session).to_dict(),
    }


def format_openai_refusal_response(refusal: RefusalResult, model: str) -> dict:
    """Format refusal response for OpenAI-compatible endpoint."""
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": refusal.response_message.content,
                },
                "finish_reason": "content_filter",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "routing": {
            "flow": refusal.route_result.flow.value,
            "decision_id": refusal.route_result.decision_id,
            "confidence": refusal.route_result.confidence,
            "was_refused": True,
        },
        "session": SessionInfo.from_session(refusal.session).to_dict(),
    }


def format_streaming_final(result, session_id: str) -> dict:
    """Format final message for streaming endpoint."""
    return {
        "messageId": result.response_message.message_id,
        "sessionId": session_id,
        "timestamp": result.response_message.server_timestamp.isoformat(),
        "markdown": result.agent_response.content,
        "routing": {
            "flow": result.route_result.flow.value,
            "decisionId": result.route_result.decision_id,
            "wasRefused": result.agent_response.was_refused,
        },
        "session": SessionInfo.from_session(result.session).to_dict(),
        "stats": {
            "llmCalls": result.total_llm_calls,
            "toolCalls": len(result.agent_response.tool_calls),
            "inputTokens": result.agent_response.input_tokens,
            "outputTokens": result.agent_response.output_tokens,
            "latencyMs": result.latency_ms,
        },
    }


# ---------------------------------------------------------------------------
# Helper: Session Info (for test questions that don't go through orchestrator)
# ---------------------------------------------------------------------------


def build_session_info(session: ChatSession) -> dict:
    """Build session info dict for API response."""
    return SessionInfo.from_session(session).to_dict()


# ---------------------------------------------------------------------------
# Test Question Handling
# ---------------------------------------------------------------------------


def handle_test_question(text: str, session_id: str, session: ChatSession) -> Response | None:
    """Check for and handle test questions. Returns Response if test question, None otherwise."""
    test_response = get_test_response(text)
    if not test_response:
        return None

    logger.info(f"🧪 Test question detected: {text}")
    return Response(
        build_test_response_data(
            test_response=test_response,
            session_id=session_id,
            message_text=text,
            session_info=build_session_info(session),
            timestamp_iso=timezone.now().isoformat(),
        )
    )


def generate_test_sse(text: str, session_id: str, session: ChatSession):
    """Generate SSE events for test questions."""
    test_response = get_test_response(text)

    routing_event = {
        "type": "routing",
        "flow": test_response["flow"],
        "decisionId": "test_decision",
        "confidence": 1.0,
        "reasonCodes": ["TEST_QUESTION"],
    }
    yield f"event: routing\ndata: {json.dumps(routing_event)}\n\n"

    final_data = build_test_response_data(
        test_response=test_response,
        session_id=session_id,
        message_text=text,
        session_info=build_session_info(session),
        timestamp_iso=timezone.now().isoformat(),
    )
    final_data["stats"] = {
        "llmCalls": 0,
        "toolCalls": 0,
        "inputTokens": 0,
        "outputTokens": 0,
        "latencyMs": 0,
    }
    yield f"event: message\ndata: {json.dumps(final_data)}\n\n"


# ---------------------------------------------------------------------------
# Chat Endpoint (Non-Streaming)
# ---------------------------------------------------------------------------


@api_view(["POST"])
def chat(request):
    """
    Handle incoming chat messages with routed Claude agent.

    POST /api/chat
    """
    # Validate request
    serializer = ChatRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {"error": "Invalid request", "details": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    data = serializer.validated_data
    session_id = data["sessionId"]
    context = data.get("context", {})

    # Get or create session for turn limit check
    session, _ = ChatSession.objects.update_or_create(
        session_id=session_id,
        defaults={
            "user_id": data["userId"],
            "last_activity": timezone.now(),
        },
    )

    # Check turn limit
    try:
        check_turn_limit(session)
    except TurnLimitReached as e:
        return Response(
            {
                "error": "turn_limit_reached",
                "message": "Turn limit reached. Start a new conversation.",
                "maxTurns": e.max_turns,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Handle test questions
    test_response = handle_test_question(data["text"], session_id, session)
    if test_response:
        return test_response

    # Load conversation history
    conversation = load_conversation_from_db(session_id)

    # Prepare turn
    ctx = prepare_turn(
        user_message=data["text"],
        message_id=data["messageId"],
        session_id=session_id,
        user_id=data["userId"],
        timestamp=data["timestamp"],
        context=context,
        conversation=conversation,
        span_name="request",
    )

    with turn_span_context(ctx):
        try:
            # Execute agent
            agent_response = execute_agent(ctx)

            # Complete turn
            result = complete_turn(ctx, agent_response)

            return Response(format_standard_response(result, session_id))

        except Exception as e:
            error_message = complete_turn_with_error(ctx, e)
            return Response(
                format_error_response(error_message, ctx.route_result, ctx.session, session_id)
            )


# ---------------------------------------------------------------------------
# Chat Stream Endpoint (SSE)
# ---------------------------------------------------------------------------


@api_view(["POST"])
def chat_stream(request):
    """
    Handle incoming chat messages with streaming progress via SSE.

    POST /api/chat/stream
    """
    # Validate request
    serializer = ChatRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {"error": "Invalid request", "details": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    data = serializer.validated_data
    session_id = data["sessionId"]
    context = data.get("context", {})

    # Get or create session for turn limit check
    session, _ = ChatSession.objects.update_or_create(
        session_id=session_id,
        defaults={
            "user_id": data["userId"],
            "last_activity": timezone.now(),
        },
    )

    # Check turn limit
    try:
        check_turn_limit(session)
    except TurnLimitReached as e:
        return Response(
            {
                "error": "turn_limit_reached",
                "message": "Turn limit reached. Start a new conversation.",
                "maxTurns": e.max_turns,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Handle test questions
    test_response = get_test_response(data["text"])
    if test_response:
        logger.info(f"🧪 [stream] Test question detected: {data['text']}")
        response = StreamingHttpResponse(
            generate_test_sse(data["text"], session_id, session),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

    # Load conversation history
    conversation = load_conversation_from_db(session_id)

    # Prepare turn
    ctx = prepare_turn(
        user_message=data["text"],
        message_id=data["messageId"],
        session_id=session_id,
        user_id=data["userId"],
        timestamp=data["timestamp"],
        context=context,
        conversation=conversation,
        span_name="stream-request",
    )

    def generate_sse():
        """Generator that yields SSE events."""
        progress_queue = queue.Queue()
        result_holder = {"response": None, "error": None}

        # Emit routing decision first
        routing_event = {
            "type": "routing",
            "flow": ctx.route_result.flow.value,
            "decisionId": ctx.route_result.decision_id,
            "confidence": ctx.route_result.confidence,
            "reasonCodes": [c.value for c in ctx.route_result.reason_codes[:5]],
        }
        yield f"event: routing\ndata: {json.dumps(routing_event)}\n\n"

        def on_progress(update: AgentProgressUpdate):
            progress_queue.put(update)

        # Capture context for thread
        parent_ctx = contextvars.copy_context()

        def run_agent_with_context():
            """Run agent in captured context to preserve Braintrust span."""
            try:
                agent_response = asyncio.run(
                    get_agent_service().send_message(
                        messages=ctx.conversation,
                        route_result=ctx.route_result,
                        on_progress=on_progress,
                        session_id=ctx.session_id,
                        user_id=ctx.user_id,
                        turn_id=ctx.turn_id,
                        **ctx.page_context.to_dict(),
                    )
                )
                result_holder["response"] = agent_response
            except Exception as e:
                result_holder["error"] = str(e)
            finally:
                progress_queue.put(None)

        def run_agent():
            parent_ctx.run(run_agent_with_context)

        agent_thread = Thread(target=run_agent, daemon=True)
        agent_thread.start()

        # Yield progress events
        while True:
            try:
                update = progress_queue.get(timeout=60)

                if update is None:
                    break

                event_data = {
                    "type": update.type,
                    "text": update.text,
                    "flow": update.flow,
                }

                if update.tool_name:
                    event_data["toolName"] = update.tool_name
                if update.tool_input:
                    event_data["toolInput"] = update.tool_input
                if update.description:
                    event_data["description"] = update.description
                if update.is_error is not None:
                    event_data["isError"] = update.is_error
                if update.output_preview:
                    event_data["outputPreview"] = update.output_preview

                yield f"event: progress\ndata: {json.dumps(event_data)}\n\n"

            except queue.Empty:
                yield ": keepalive\n\n"

        agent_thread.join(timeout=5)

        # Handle error
        if result_holder["error"]:
            complete_turn_with_error(ctx, Exception(result_holder["error"]))
            ctx.request_span.end()
            yield f"event: error\ndata: {json.dumps({'error': result_holder['error']})}\n\n"
            return

        # Complete turn
        agent_response = result_holder["response"]
        result = complete_turn(ctx, agent_response)
        ctx.request_span.end()

        yield f"event: message\ndata: {json.dumps(format_streaming_final(result, session_id))}\n\n"

    response = StreamingHttpResponse(generate_sse(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


# ---------------------------------------------------------------------------
# OpenAI-Compatible Endpoint
# ---------------------------------------------------------------------------


def _openai_error_response(message: str, error_type: str, code: str, status_code: int):
    """Return an OpenAI-style error response."""
    return Response(
        {
            "error": {
                "message": message,
                "type": error_type,
                "code": code,
            }
        },
        status=status_code,
    )


@api_view(["POST"])
def openai_chat_completions(request):
    """
    OpenAI-compatible chat completions endpoint.

    POST /api/v1/chat/completions
    """
    # Validate request
    serializer = OpenAIChatRequestSerializer(data=request.data)
    if not serializer.is_valid():
        first_error = next(iter(serializer.errors.values()))[0]
        return _openai_error_response(
            message=f"Invalid request: {first_error}",
            error_type="invalid_request_error",
            code="invalid_request",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    data = serializer.validated_data
    messages = data["messages"]
    model = data["model"]

    # Extract last user message
    user_message = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"),
        None,
    )
    if not user_message:
        return _openai_error_response(
            message="No user message found in messages array",
            error_type="invalid_request_error",
            code="missing_user_message",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # Generate identifiers with bt- prefix
    session_id = f"bt-{uuid.uuid4().hex[:12]}"
    user_id = "bt-braintrust-playground"
    message_id = f"msg-{uuid.uuid4().hex[:12]}"

    # Create session
    session, _ = ChatSession.objects.update_or_create(
        session_id=session_id,
        defaults={
            "user_id": user_id,
            "last_activity": timezone.now(),
        },
    )

    # Check turn limit
    try:
        check_turn_limit(session)
    except TurnLimitReached:
        return _openai_error_response(
            message="Turn limit reached. Start a new conversation.",
            error_type="invalid_request_error",
            code="turn_limit_reached",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # Build conversation from OpenAI messages
    conversation = [ConversationMessage(role=m["role"], content=m["content"]) for m in messages]

    # OpenAI-specific context
    context = {
        "pageUrl": "https://braintrust.dev/playground",
        "clientVersion": "openai-compat-1.0",
    }

    # Prepare turn
    ctx = prepare_turn(
        user_message=user_message,
        message_id=message_id,
        session_id=session_id,
        user_id=user_id,
        timestamp=timezone.now(),
        context=context,
        conversation=conversation,
        span_name="openai-compat-request",
    )

    # Override source to indicate braintrust origin (not component or api)
    ctx.page_context.source = "braintrust"

    # Add braintrust tag
    ctx.request_span.log(tags=["braintrust", "openai-compat"])

    with turn_span_context(ctx):
        # Handle early refusal (before calling agent)
        if ctx.route_result.flow == Flow.REFUSE:
            refusal = complete_turn_with_refusal(ctx)
            ctx.request_span.log(tags=["braintrust"])  # Add braintrust tag to refusal
            return Response(format_openai_refusal_response(refusal, model))

        try:
            # Execute agent
            agent_response = execute_agent(ctx)

            # Complete turn (includes summary)
            result = complete_turn(ctx, agent_response, include_summary=True)

            return Response(format_openai_response(result, model))

        except Exception as e:
            logger.error(f"[openai-compat] Agent error: {e}", exc_info=True)
            complete_turn_with_error(ctx, e)
            return _openai_error_response(
                message=f"Internal error: {e!s}",
                error_type="internal_error",
                code="agent_error",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ---------------------------------------------------------------------------
# History Endpoint
# ---------------------------------------------------------------------------


@api_view(["GET"])
def history(request):
    """
    Get conversation history with routing metadata.

    GET /api/history?userId=...&sessionId=...&before=...&limit=...
    """
    user_id = request.query_params.get("userId")
    session_id = request.query_params.get("sessionId")
    before = request.query_params.get("before")
    limit = min(int(request.query_params.get("limit", 20)), 100)

    if not user_id or not session_id:
        return Response(
            {"error": "userId and sessionId are required"}, status=status.HTTP_400_BAD_REQUEST
        )

    logger.info(f"📜 history user={user_id} session={session_id[:20]}... limit={limit}")

    queryset = ChatMessage.objects.filter(
        user_id=user_id,
        session_id=session_id,
    )

    if before:
        try:
            before_dt = datetime.fromisoformat(before.replace("Z", "+00:00"))
            queryset = queryset.filter(server_timestamp__lt=before_dt)
        except ValueError:
            return Response(
                {"error": "Invalid before timestamp"}, status=status.HTTP_400_BAD_REQUEST
            )

    messages = list(queryset.order_by("-server_timestamp")[: limit + 1])

    has_more = len(messages) > limit
    messages = messages[:limit]
    messages.reverse()

    serializer = HistoryMessageSerializer(messages, many=True)

    # Get session info
    try:
        session = ChatSession.objects.get(session_id=session_id)
        session_info = {
            "currentFlow": session.current_flow,
            "turnCount": session.turn_count or 0,
            "totalTokens": (session.total_input_tokens or 0) + (session.total_output_tokens or 0),
            "maxTurns": settings.MAX_TURNS,
            "limitReached": (session.turn_count or 0) >= settings.MAX_TURNS,
        }
    except ChatSession.DoesNotExist:
        session_info = None

    return Response(
        {
            "messages": serializer.data,
            "hasMore": has_more,
            "session": session_info,
        }
    )


# ---------------------------------------------------------------------------
# Admin Endpoints
# ---------------------------------------------------------------------------


@api_view(["POST"])
def reload_prompts(request):
    """
    Reload prompts from Braintrust without restarting the server.

    POST /api/admin/reload-prompts
    """
    try:
        prompt_service = get_prompt_service()
        prompt_service.invalidate_cache()

        logger.info("🔄 Prompts cache invalidated")

        return Response(
            {
                "success": True,
                "message": "Prompt cache invalidated. New prompts will be fetched on next request.",
            }
        )
    except Exception as e:
        logger.error(f"❌ Failed to reload prompts: {e}")
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
def health(request):
    """
    Health check endpoint.

    GET /api/health
    """
    return Response(
        {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "services": {
                "agent": get_agent_service() is not None,
                "router": get_router() is not None,
                "braintrust": True,
            },
        }
    )
