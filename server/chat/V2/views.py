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
import os
import queue
import time

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
from .checks import run_pre_flight_checks
from .logging import get_turn_logging_service
from .services import (
    create_or_get_session,
    load_session_summary,
    save_user_message,
)
from .summarization import get_summary_service

logger = logging.getLogger("chat")


def build_session_info(session) -> dict:
    """Build session info dict for API response."""
    turn_count = session.turn_count or 0
    return {
        "turnCount": turn_count,
    }


def _create_traced_executor() -> concurrent.futures.Executor:
    """Create a ThreadPoolExecutor that preserves Braintrust span context.

    Braintrust's TracedThreadPoolExecutor copies the current trace span
    into the worker thread, so LLM calls on the background thread appear
    as children of the request-level span. Falls back to a plain executor
    if Braintrust isn't installed.
    """
    try:
        import braintrust

        traced_executor = getattr(braintrust, "TracedThreadPoolExecutor", None)
        if traced_executor:
            return traced_executor(max_workers=1)
    except Exception:
        pass

    return concurrent.futures.ThreadPoolExecutor(max_workers=1)


def _flush_braintrust():
    """Flush pending Braintrust spans so they're sent before the request ends."""
    try:
        import braintrust

        braintrust.flush()
    except Exception:
        pass


# Global services (initialized lazily)
_bt_logger = None


def get_braintrust_logger():
    """Get or create a Braintrust logger for feedback."""
    global _bt_logger
    api_key = os.environ.get("BRAINTRUST_API_KEY")
    project = os.environ.get("BRAINTRUST_PROJECT", "On Site Agent")
    if not api_key:
        return None

    try:
        import braintrust

        # init_logger also sets the current logger for this thread/context
        _bt_logger = braintrust.init_logger(project=project, api_key=api_key)
        return _bt_logger
    except Exception as e:
        logger.warning(f"⚠️  Failed to initialize Braintrust logger: {e}")
        return None


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

    context = data.get("context", {})
    page_url = context.get("pageUrl", "")
    prompt_slugs = data.get("promptSlugs") or {}
    turn_id = ChatMessage.generate_turn_id()

    # Create or get session with ownership validation
    session, _ = create_or_get_session(data["sessionId"], actor)

    # Pre-flight checks (guardrail)
    preflight = run_pre_flight_checks(data["text"], session)
    if not preflight.passed:

        def generate_rejection_sse():
            yield f"event: guardrail\ndata: {json.dumps({'blocked': True, 'message': preflight.rejection_message, 'reason': preflight.rejection_reason, 'type': preflight.rejection_type})}\n\n"

        resp = StreamingHttpResponse(generate_rejection_sse(), content_type="text/event-stream")
        resp["Cache-Control"] = "no-cache"
        resp["X-Accel-Buffering"] = "no"
        return resp

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
                agent = get_agent_service()
                result_holder["response"] = asyncio.run(
                    agent.send_message(
                        messages=conversation,
                        core_prompt_id=core_prompt_slug,
                        on_progress=on_progress,
                        context=msg_context,
                    )
                )
            except Exception as e:
                result_holder["error"] = str(e)
            finally:
                _flush_braintrust()
                # Sentinel: signals the main thread that the agent is done
                progress_queue.put(None)

        # Preserve contextvars (e.g. Braintrust trace context) into the thread
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
                error_text="I'm sorry, I encountered an error processing your request.",
            )

            user_message.response_message = error_msg
            user_message.save(update_fields=["response_message"])

            yield f"event: error\ndata: {json.dumps({'error': 'An internal error occurred.'})}\n\n"
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
    bt_logger = get_braintrust_logger()
    if not bt_logger:
        return Response(
            {"error": "braintrust_unavailable"},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    metadata = {
        "user_id": data.get("userId", ""),
        "session_id": data.get("sessionId", ""),
        "message_id": data.get("messageId", ""),
    }
    comment = (data.get("comment") or "").strip() or None
    scores = {"user_rating": data["score"]}

    try:
        if hasattr(bt_logger, "log_feedback"):
            bt_logger.log_feedback(
                id=data["traceId"],
                scores=scores,
                comment=comment,
                metadata=metadata,
            )
        elif hasattr(bt_logger, "logFeedback"):
            bt_logger.logFeedback(
                {
                    "id": data["traceId"],
                    "scores": scores,
                    "comment": comment,
                    "metadata": metadata,
                }
            )
        else:
            raise AttributeError("Braintrust logger does not support feedback logging")
    except Exception as e:
        logger.error(f"❌ Failed to log feedback: {e}")
        return Response(
            {"error": "feedback_log_failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    return Response({"success": True})
