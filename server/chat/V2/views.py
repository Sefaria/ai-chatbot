"""
V2 streaming chat endpoint — the primary API used by the Svelte frontend.

Request flow:
    POST /api/v2/chat/stream
    → authenticate via userId token
    → create/resume session, load conversation summary
    → save user message to DB
    → return SSE StreamingHttpResponse that:
        1. Runs the agent on a background thread
        2. Streams progress events (tool_start, tool_end, status) in real-time
        3. On success: updates summary, persists response, yields final "message" event
        4. On error: persists error message, yields "error" event

Also includes:
    GET  /api/v2/prompts/defaults — returns default Braintrust prompt slugs
    POST /api/v2/chat/feedback    — logs user feedback to Braintrust
"""

import asyncio
import concurrent.futures
import contextvars
import json
import logging
import queue
import time

import braintrust
from django.conf import settings
from django.http import StreamingHttpResponse
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
from ..serializers import ChatRequestSerializer, FeedbackRequestSerializer
from .agent import AgentProgressUpdate, ConversationMessage, MessageContext, get_agent_service
from .agent.tracing_guard import suppress_tracing
from .logging import get_turn_logging_service
from .origin import resolve_origin
from .prompts.prompt_fragments import ERROR_FALLBACK_MESSAGE, INTERNAL_ERROR_MESSAGE
from .sentry import capture_exception
from .services import (
    create_or_get_session,
    load_session_summary,
    save_user_message,
)
from .summarization import get_summary_service
from .utils import flush_braintrust as _flush_braintrust
from .utils import get_braintrust_config

logger = logging.getLogger("chat")


def build_session_info(session) -> dict:
    """Build session info dict for API response."""
    return {
        "turnCount": session.turn_count,
        "maxPrompts": settings.MAX_PROMPTS,
        "maxInputChars": settings.MAX_INPUT_CHARS,
    }


_bt_config = get_braintrust_config()
_bt_logger = None


def _get_bt_logger():
    """Lazy-init Braintrust logger so load-test-only processes never touch BT."""
    if not settings.BRAINTRUST_LOGGING_ENABLED:
        return None
    global _bt_logger
    if _bt_logger is None:
        _bt_logger = braintrust.init_logger(project=_bt_config.project, api_key=_bt_config.api_key)
    return _bt_logger


def _create_traced_executor() -> concurrent.futures.Executor:
    """Create a ThreadPoolExecutor that preserves Braintrust span context.

    Braintrust's TracedThreadPoolExecutor copies the current trace span
    into the worker thread, so LLM calls on the background thread appear
    as children of the request-level span.
    """
    return braintrust.TracedThreadPoolExecutor(max_workers=1)


@api_view(["POST"])
def chat_stream_v2(request):
    """
    Simplified streaming chat endpoint with summary-based multi-turn context.

    POST /api/v2/chat/stream
    """
    start_time = time.time()

    serializer = ChatRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {"error": "Invalid request", "details": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    data = serializer.validated_data

    try:
        actor = authenticate_request(request, data)
    except UserTokenExpired:
        logger.warning("expired userId token")
        return Response({"error": "userId_expired"}, status=status.HTTP_401_UNAUTHORIZED)
    except (InvalidUserToken, AuthenticationRequired) as exc:
        logger.warning(f"invalid userId token: {exc}")
        return Response({"error": "invalid_userId"}, status=status.HTTP_401_UNAUTHORIZED)

    is_load_test = data.get("isLoadTest", False)
    context = data.get("context", {})
    page_url = context.get("pageUrl", "")
    prompt_slugs = data.get("promptSlugs") or {}
    turn_id = ChatMessage.generate_turn_id()

    # Create or get session with ownership validation
    session, _ = create_or_get_session(data["sessionId"], actor)

    # Enforce turn limit
    if session.turn_count >= settings.MAX_PROMPTS:
        return Response(
            {
                "error": "turn_limit_reached",
                "message": "Conversation limit reached. Please start a new chat.",
            },
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    # Enforce input length limit
    if len(data["text"]) > settings.MAX_INPUT_CHARS:
        return Response(
            {
                "error": "input_too_long",
                "message": f"Message exceeds maximum length of {settings.MAX_INPUT_CHARS} characters.",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Load summary for this session (if any)
    summary_text = load_session_summary(session)

    # Only core prompt slug is used for v2 streaming.
    core_prompt_slug = (prompt_slugs.get("corePromptSlug") or "").strip()
    if not core_prompt_slug:
        core_prompt_slug = settings.CORE_PROMPT_SLUG

    # Save user message
    user_message = save_user_message(
        session=session,
        actor=actor,
        message_id=data["messageId"],
        turn_id=turn_id,
        content=data["text"],
        client_timestamp=data["timestamp"],
        locale=context.get("locale", ""),
        client_version=context.get("clientVersion", ""),
    )

    msg_context = MessageContext(
        summary_text=summary_text,
        page_url=page_url or None,
        session_id=data["sessionId"],
        # Note: Anthropic endpoint reads origin from X-Origin header (anthropic_views.py).
        origin=resolve_origin(context.get("origin")),
    )

    def generate_sse():
        """Generator that yields SSE events for a single chat turn.

        Runs the agent on a background thread (since the Agent SDK is async)
        and bridges progress updates to the main thread via a queue. The main
        thread consumes the queue and yields SSE events to the client.

        Lifecycle:
          1. Start agent on background thread
          2. Stream progress events as they arrive
          3. Wait for agent to finish
          4. On error  → log, persist error message, yield error event
          5. On success → update summary, persist messages, yield final event
        """
        progress_queue = queue.Queue()
        result_holder = {"response": None, "error": None}

        def on_progress(update: AgentProgressUpdate):
            progress_queue.put(update)

        def run_agent():
            """Background thread: runs the async agent and captures the result."""
            try:
                conversation = [ConversationMessage(role="user", content=data["text"])]
                agent = get_agent_service(is_load_test=is_load_test)

                async def _send():
                    return await agent.send_message(
                        messages=conversation,
                        core_prompt_id=core_prompt_slug,
                        on_progress=on_progress,
                        context=msg_context,
                    )

                if is_load_test:
                    with suppress_tracing():
                        result_holder["response"] = asyncio.run(_send())
                else:
                    result_holder["response"] = asyncio.run(_send())
            except Exception as e:
                logger.exception("Agent error in streaming endpoint")
                capture_exception(
                    e,
                    endpoint="chat_stream_v2",
                    session_id=data["sessionId"],
                    turn_id=turn_id,
                )
                result_holder["error"] = str(e)
            finally:
                if not is_load_test:
                    _flush_braintrust()
                # Sentinel: signals the main thread that the agent is done
                progress_queue.put(None)

        # For load tests, run in a clean context to prevent Braintrust span
        # leaking from the main thread.  Normal requests preserve contextvars
        # so Braintrust traces propagate correctly.
        if is_load_test:
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = executor.submit(run_agent)
        else:
            ctx = contextvars.copy_context()
            executor = _create_traced_executor()
            future = executor.submit(ctx.run, run_agent)

        # --- Stream progress events to the client ---
        while True:
            try:
                update = progress_queue.get(timeout=60)

                # None sentinel means the agent thread finished
                if update is None:
                    break

                # Build the SSE payload, including optional tool-call fields
                event_data = {
                    "type": update.type,
                    "text": update.text,
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
                # No update in 60s — send a keepalive to prevent
                # proxies/load-balancers from closing the connection
                yield ": keepalive\n\n"

        # --- Agent finished — clean up the thread ---
        try:
            future.result(timeout=5)
        except Exception:
            pass
        executor.shutdown(wait=False)

        latency_ms = int((time.time() - start_time) * 1000)

        # --- Error path: persist an error message and notify client ---
        if result_holder["error"]:
            logger.error(f"Agent error: {result_holder['error']}")

            logging_service = get_turn_logging_service()
            error_msg = logging_service.record_error_message(
                session_id=data["sessionId"],
                actor=actor,
                turn_id=turn_id,
                latency_ms=latency_ms,
                error_text=ERROR_FALLBACK_MESSAGE,
            )

            user_message.response_message = error_msg
            user_message.save(update_fields=["response_message"])

            yield f"event: error\ndata: {json.dumps({'error': INTERNAL_ERROR_MESSAGE})}\n\n"
            return

        # --- Success path: update summary, persist turn, yield final event ---
        agent_response = result_holder["response"]

        summary_service = get_summary_service()
        new_summary = summary_service.update_summary(
            session=session,
            new_user_message=data["text"],
            new_assistant_response=agent_response.content,
        )
        logging_service = get_turn_logging_service()
        logging_result = logging_service.finalize_success(
            session=session,
            user_message=user_message,
            agent_response=agent_response,
            latency_ms=latency_ms,
            model_name=agent_response.model or "unknown",
            summary_text=new_summary.to_prompt_text(),
        )

        response_message = logging_result.response_message

        session.refresh_from_db()

        final_data = {
            "messageId": response_message.message_id,
            "sessionId": data["sessionId"],
            "timestamp": response_message.server_timestamp.isoformat(),
            "markdown": agent_response.content,
            "traceId": agent_response.trace_id,
            "toolCalls": agent_response.tool_calls,
            "session": build_session_info(session),
            "stats": logging_result.stats,
        }

        yield f"event: message\ndata: {json.dumps(final_data)}\n\n"

    response = StreamingHttpResponse(generate_sse(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"

    return response


@api_view(["GET"])
def prompt_defaults(request):
    """Return default Braintrust prompt slugs for client settings."""
    return Response(
        {
            "corePromptSlug": settings.CORE_PROMPT_SLUG,
        }
    )


@api_view(["POST"])
def chat_feedback_v2(request):
    """Capture user feedback for the latest chat trace in Braintrust."""
    serializer = FeedbackRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {"error": "Invalid request", "details": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    data = serializer.validated_data
    bt_logger = _get_bt_logger()

    feedback_reason = (data.get("feedbackReason") or "").strip()
    score = data["score"]
    comment = (data.get("comment") or "").strip()

    try:
        # Log feedback in Braintrust's feedback system
        # Update trace metadata so feedback appears in the same metadata blob in the UI
        # (that blob is span metadata set at message-creation time)
        # This allows us to easily view feedback metadata in the UI.
        if bt_logger is not None:
            feedback_metadata = {
                "feedback": score,
                "feedback_reason": feedback_reason,
                "feedback_comment": comment,
                "session_id": data["sessionId"],
                "user_id": data["userId"],
                "message_id": data["messageId"],
            }
            try:
                bt_logger.update_span(id=data["traceId"], metadata=feedback_metadata)
            except Exception as _e:
                logger.debug("Could not update span metadata with feedback metadata: %s", _e)
    except Exception as e:
        logger.error(f"❌ Failed to log feedback: {e}")
        return Response(
            {"error": "feedback_log_failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    return Response({"success": True})
