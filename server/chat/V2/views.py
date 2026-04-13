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
from urllib.parse import urlsplit

import braintrust
from django.conf import settings
from django.core.cache import cache
from django.http import StreamingHttpResponse
from django.utils import timezone
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
from ..serializers import (
    ChatRequestSerializer,
    ClientStreamEventSerializer,
    FeedbackRequestSerializer,
    RecoveryRequestSerializer,
)
from .agent import AgentProgressUpdate, ConversationMessage, MessageContext, get_agent_service
from .agent.tracing_guard import suppress_tracing
from .logging import get_turn_logging_service
from .origin import resolve_origin
from .prompts.prompt_fragments import ERROR_FALLBACK_MESSAGE, INTERNAL_ERROR_MESSAGE
from .sentry import capture_exception, capture_message
from .services import (
    create_or_get_session,
    load_session_summary,
    save_user_message,
)
from .summarization import get_summary_service
from .utils import flush_braintrust as _flush_braintrust
from .utils import get_braintrust_config

logger = logging.getLogger("chat")

STREAM_KEEPALIVE_INTERVAL_SECONDS = 60
STREAM_HEARTBEAT_TIMEOUT_MS = 90_000
STREAM_PROGRESS_QUEUE_MAXSIZE = 100
CLIENT_STREAM_EVENT_RATE_LIMIT = 30
CLIENT_STREAM_EVENT_WINDOW_SECONDS = 60


def build_session_info(session) -> dict:
    """Build session info dict for API response."""
    return {
        "turnCount": session.turn_count or 0,
    }


def _compute_turn_count(session_id: str) -> int:
    """Count completed turns directly from message linkage."""
    return ChatMessage.objects.filter(
        session_id=session_id,
        role=ChatMessage.Role.USER,
        response_message__isnull=False,
        response_message__status=ChatMessage.Status.SUCCESS,
    ).count()


def _mark_turn_started(user_message_id: int) -> None:
    now = timezone.now()
    ChatMessage.objects.filter(id=user_message_id).update(
        processing_state=ChatMessage.ProcessingState.STARTED,
        processing_started_at=now,
        processing_heartbeat_at=now,
        processing_finished_at=None,
        processing_error="",
    )


def _mark_turn_running(user_message_id: int) -> None:
    ChatMessage.objects.filter(id=user_message_id).update(
        processing_state=ChatMessage.ProcessingState.RUNNING,
        processing_heartbeat_at=timezone.now(),
        processing_error="",
    )


def _mark_turn_heartbeat(user_message_id: int) -> None:
    ChatMessage.objects.filter(
        id=user_message_id,
        processing_state__in=[
            ChatMessage.ProcessingState.STARTED,
            ChatMessage.ProcessingState.RUNNING,
        ],
    ).update(
        processing_state=ChatMessage.ProcessingState.RUNNING,
        processing_heartbeat_at=timezone.now(),
    )


def _mark_turn_completed(user_message_id: int) -> None:
    now = timezone.now()
    ChatMessage.objects.filter(id=user_message_id).update(
        processing_state=ChatMessage.ProcessingState.COMPLETED,
        processing_heartbeat_at=now,
        processing_finished_at=now,
        processing_error="",
    )


def _mark_turn_failed(user_message_id: int, error: str = "") -> None:
    now = timezone.now()
    ChatMessage.objects.filter(id=user_message_id).update(
        processing_state=ChatMessage.ProcessingState.FAILED,
        processing_heartbeat_at=now,
        processing_finished_at=now,
        processing_error=error or "",
    )


def _build_recovery_status_payload(user_message: ChatMessage) -> dict:
    heartbeat_at = user_message.processing_heartbeat_at or user_message.processing_started_at
    payload = {
        "requestFound": True,
        "processingState": user_message.processing_state or "",
        "heartbeatTimeoutMs": STREAM_HEARTBEAT_TIMEOUT_MS,
    }
    if heartbeat_at is not None:
        heartbeat_age_ms = max(int((timezone.now() - heartbeat_at).total_seconds() * 1000), 0)
        payload["lastHeartbeatAt"] = heartbeat_at.isoformat()
        payload["heartbeatAgeMs"] = heartbeat_age_ms
        payload["status"] = (
            "stale" if heartbeat_age_ms >= STREAM_HEARTBEAT_TIMEOUT_MS else "running"
        )
        return payload

    payload["status"] = "pending"
    return payload


def _build_response_payload(
    *,
    response_message: ChatMessage,
    session_id: str,
    turn_count: int,
    stats: dict,
    trace_id: str | None = None,
    recovered: bool = False,
) -> dict:
    return {
        "messageId": response_message.message_id,
        "sessionId": session_id,
        "timestamp": response_message.server_timestamp.isoformat(),
        "markdown": response_message.content,
        "traceId": trace_id,
        "toolCalls": response_message.tool_calls_data or [],
        "session": {"turnCount": turn_count},
        "stats": stats,
        "recovered": recovered,
        "status": response_message.status,
    }


def _is_stream_break_test_enabled() -> bool:
    return bool(getattr(settings, "CHAT_STREAM_BREAK_TESTING_ENABLED", settings.DEBUG))


def _sanitize_page_url(page_url: str) -> str:
    if not page_url:
        return ""
    parsed = urlsplit(page_url)
    return parsed.path or ""


def _is_client_event_rate_limited(user_id: str) -> bool:
    bucket = int(time.time() // CLIENT_STREAM_EVENT_WINDOW_SECONDS)
    cache_key = f"chat_client_event:{user_id}:{bucket}"
    if cache.add(cache_key, 1, timeout=CLIENT_STREAM_EVENT_WINDOW_SECONDS + 5):
        return False
    try:
        count = cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, timeout=CLIENT_STREAM_EVENT_WINDOW_SECONDS + 5)
        return False
    return count > CLIENT_STREAM_EVENT_RATE_LIMIT


def _authenticate_actor_or_response(request, data):
    """Authenticate a request payload or return an error Response."""
    try:
        return authenticate_request(request, data)
    except UserTokenExpired:
        logger.warning("expired userId token")
        return Response({"error": "userId_expired"}, status=status.HTTP_401_UNAUTHORIZED)
    except (InvalidUserToken, AuthenticationRequired) as exc:
        logger.warning(f"invalid userId token: {exc}")
        return Response({"error": "invalid_userId"}, status=status.HTTP_401_UNAUTHORIZED)


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

    actor = _authenticate_actor_or_response(request, data)
    if isinstance(actor, Response):
        return actor

    is_load_test = data.get("isLoadTest", False)
    context = data.get("context", {})
    page_url = context.get("pageUrl", "")
    prompt_slugs = data.get("promptSlugs") or {}
    turn_id = ChatMessage.generate_turn_id()

    # Create or get session with ownership validation
    session, _ = create_or_get_session(data["sessionId"], actor)

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
        page_url=page_url,
        locale=context.get("locale", ""),
        client_version=context.get("clientVersion", ""),
    )
    _mark_turn_started(user_message.id)

    msg_context = MessageContext(
        summary_text=summary_text,
        page_url=page_url or None,
        session_id=data["sessionId"],
        # Note: Anthropic endpoint reads origin from X-Origin header (anthropic_views.py).
        origin=resolve_origin(context.get("origin")),
        is_staff=context.get("isStaff", False),
        user_id=actor.user_id,
        encrypted_user_token=actor.encrypted_token,
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
        progress_queue = queue.Queue(maxsize=STREAM_PROGRESS_QUEUE_MAXSIZE)
        result_holder = {"response": None, "error": None}
        assistant_persisted = False
        final_event_sent = False
        stream_closed = False

        def on_progress(update: AgentProgressUpdate):
            if stream_closed:
                return
            try:
                progress_queue.put(update, timeout=0.1)
            except queue.Full:
                logger.warning(
                    "Dropping stream progress update after queue saturation",
                    extra={"session_id": data["sessionId"], "turn_id": turn_id},
                )

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
                while not stream_closed:
                    try:
                        progress_queue.put(None, timeout=0.1)
                        break
                    except queue.Full:
                        continue

        # For load tests, run in a clean context to prevent Braintrust span
        # leaking from the main thread.  Normal requests preserve contextvars
        # so Braintrust traces propagate correctly.
        if is_load_test:
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            _mark_turn_running(user_message.id)
            future = executor.submit(run_agent)
        else:
            ctx = contextvars.copy_context()
            executor = _create_traced_executor()
            _mark_turn_running(user_message.id)
            future = executor.submit(ctx.run, run_agent)

        try:
            # --- Stream progress events to the client ---
            while True:
                try:
                    update = progress_queue.get(timeout=STREAM_KEEPALIVE_INTERVAL_SECONDS)

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

                    _mark_turn_heartbeat(user_message.id)
                    yield f"event: progress\ndata: {json.dumps(event_data)}\n\n"

                except queue.Empty:
                    # No update in 60s — send a keepalive to prevent
                    # proxies/load-balancers from closing the connection
                    _mark_turn_heartbeat(user_message.id)
                    yield ": keepalive\n\n"

            latency_ms = int((time.time() - start_time) * 1000)
            logging_service = get_turn_logging_service()

            # --- Error path: persist an error message and notify client ---
            if result_holder["error"]:
                logger.error(f"Agent error: {result_holder['error']}")

                error_msg = logging_service.record_error_message(
                    session_id=data["sessionId"],
                    actor=actor,
                    turn_id=turn_id,
                    latency_ms=latency_ms,
                    error_text=ERROR_FALLBACK_MESSAGE,
                )

                user_message.response_message = error_msg
                user_message.save(update_fields=["response_message"])
                assistant_persisted = True
                _mark_turn_failed(user_message.id, result_holder["error"])

                logger.info(
                    "Assistant row persisted after agent error",
                    extra={
                        "session_id": data["sessionId"],
                        "turn_id": turn_id,
                        "response_message_id": error_msg.message_id,
                    },
                )

                yield f"event: error\ndata: {json.dumps({'error': INTERNAL_ERROR_MESSAGE})}\n\n"
                final_event_sent = True
                logger.info(
                    "Final SSE error sent",
                    extra={"session_id": data["sessionId"], "turn_id": turn_id},
                )
                return

            # --- Success path: persist first, then do non-fatal session/summary work ---
            agent_response = result_holder["response"]
            logger.info(
                "Agent completed",
                extra={
                    "session_id": data["sessionId"],
                    "turn_id": turn_id,
                    "trace_id": agent_response.trace_id,
                },
            )

            logging_result = logging_service.persist_assistant_response(
                user_message=user_message,
                agent_response=agent_response,
                latency_ms=latency_ms,
                model_name=agent_response.model or "unknown",
            )
            response_message = logging_result.response_message
            assistant_persisted = True
            _mark_turn_completed(user_message.id)

            logger.info(
                "Assistant row persisted",
                extra={
                    "session_id": data["sessionId"],
                    "turn_id": turn_id,
                    "response_message_id": response_message.message_id,
                },
            )

            summary_text = None
            try:
                summary_service = get_summary_service()
                new_summary = summary_service.update_summary(
                    session=session,
                    new_user_message=data["text"],
                    new_assistant_response=agent_response.content,
                )
                summary_text = new_summary.to_prompt_text()
            except Exception as exc:
                logger.exception("Summary update failed after agent success")
                capture_exception(
                    exc,
                    endpoint="chat_stream_v2",
                    session_id=data["sessionId"],
                    turn_id=turn_id,
                    phase="summary_after_success",
                    response_message_id=response_message.message_id,
                )

            try:
                logging_service.update_session_success(
                    session=session,
                    user_message=user_message,
                    agent_response=agent_response,
                    summary_text=summary_text,
                )
                session.refresh_from_db()
            except Exception as exc:
                logger.exception("Session update failed after assistant persist")
                capture_exception(
                    exc,
                    endpoint="chat_stream_v2",
                    session_id=data["sessionId"],
                    turn_id=turn_id,
                    phase="session_update_after_success",
                    response_message_id=response_message.message_id,
                )

            final_data = _build_response_payload(
                response_message=response_message,
                session_id=data["sessionId"],
                turn_count=_compute_turn_count(data["sessionId"]),
                trace_id=agent_response.trace_id,
                stats=logging_result.stats,
            )

            if context.get("forceStreamBreakBeforeFinal", False) and _is_stream_break_test_enabled():
                test_exc = RuntimeError("Forced stream break before final SSE for testing")
                logger.error(
                    "Forcing stream break before final SSE for testing",
                    extra={
                        "session_id": data["sessionId"],
                        "turn_id": turn_id,
                        "response_message_id": response_message.message_id,
                    },
                )
                capture_exception(
                    test_exc,
                    endpoint="chat_stream_v2",
                    session_id=data["sessionId"],
                    turn_id=turn_id,
                    phase="forced_break_before_final_sse",
                    response_message_id=response_message.message_id,
                )
                raise test_exc

            yield f"event: message\ndata: {json.dumps(final_data)}\n\n"
            final_event_sent = True
            logger.info(
                "Final SSE sent",
                extra={
                    "session_id": data["sessionId"],
                    "turn_id": turn_id,
                    "response_message_id": response_message.message_id,
                },
            )
        except (BrokenPipeError, ConnectionResetError, GeneratorExit):
            if not final_event_sent:
                logger.warning(
                    "Client disconnected before final send",
                    extra={
                        "session_id": data["sessionId"],
                        "turn_id": turn_id,
                        "assistant_persisted": assistant_persisted,
                        "agent_completed": result_holder["response"] is not None,
                    },
                )
            raise
        finally:
            stream_closed = True
            if not future.done():
                future.cancel()
            elif future.done():
                try:
                    future.result(timeout=0)
                except Exception:
                    pass
            executor.shutdown(wait=False)

    response = StreamingHttpResponse(generate_sse(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"

    return response


@api_view(["POST"])
def chat_recover_v2(request):
    """Recover a response that may have been persisted after stream failure."""
    serializer = RecoveryRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {"error": "Invalid request", "details": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    data = serializer.validated_data
    actor = _authenticate_actor_or_response(request, data)
    if isinstance(actor, Response):
        return actor

    user_message = (
        ChatMessage.objects.select_related("response_message")
        .filter(
            message_id=data["messageId"],
            session_id=data["sessionId"],
            user_id=actor.user_id,
            role=ChatMessage.Role.USER,
        )
        .first()
    )
    if user_message is None:
        return Response(
            {
                "status": "pending",
                "requestFound": False,
                "heartbeatTimeoutMs": STREAM_HEARTBEAT_TIMEOUT_MS,
            }
        )

    response_message = user_message.response_message
    if response_message is None and user_message.turn_id:
        response_message = (
            ChatMessage.objects.filter(
                session_id=user_message.session_id,
                turn_id=user_message.turn_id,
                role=ChatMessage.Role.ASSISTANT,
            )
            .order_by("-server_timestamp")
            .first()
        )
        if response_message is not None:
            user_message.response_message = response_message
            user_message.save(update_fields=["response_message"])

    if response_message is None:
        if user_message.processing_state in (
            ChatMessage.ProcessingState.STARTED,
            ChatMessage.ProcessingState.RUNNING,
        ):
            return Response(_build_recovery_status_payload(user_message))
        if user_message.processing_state == ChatMessage.ProcessingState.FAILED:
            return Response(
                {
                    "status": "failed",
                    "requestFound": True,
                    "processingState": user_message.processing_state,
                    "heartbeatTimeoutMs": STREAM_HEARTBEAT_TIMEOUT_MS,
                    "error": user_message.processing_error or "",
                }
            )
        return Response(
            {
                "status": "pending",
                "requestFound": True,
                "processingState": user_message.processing_state or "",
                "heartbeatTimeoutMs": STREAM_HEARTBEAT_TIMEOUT_MS,
            }
        )

    stats = get_turn_logging_service().build_stats_from_message(response_message)
    payload = _build_response_payload(
        response_message=response_message,
        session_id=user_message.session_id,
        turn_count=_compute_turn_count(user_message.session_id),
        stats=stats,
        recovered=True,
    )
    recovery_status = (
        "failed" if response_message.status == ChatMessage.Status.FAILED else "complete"
    )

    return Response(
        {
            "status": recovery_status,
            "requestFound": True,
            "message": payload,
            "error": response_message.content
            if response_message.status == ChatMessage.Status.FAILED
            else "",
            "processingState": user_message.processing_state or "",
            "heartbeatTimeoutMs": STREAM_HEARTBEAT_TIMEOUT_MS,
        }
    )


@api_view(["POST"])
def chat_client_event_v2(request):
    """Ingest browser-side stream telemetry into server logs and Sentry."""
    serializer = ClientStreamEventSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {"error": "Invalid request", "details": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    data = serializer.validated_data
    actor = _authenticate_actor_or_response(request, data)
    if isinstance(actor, Response):
        return actor

    context = data.get("context") or {}
    if _is_client_event_rate_limited(actor.user_id):
        return Response({"success": True, "sampled": False})

    log_payload = {
        "session_id": data["sessionId"],
        "message_id": data["messageId"],
        "user_id": actor.user_id,
        "event": data["event"],
        "error": data.get("error", ""),
        "page_url": _sanitize_page_url(context.get("pageUrl", "")),
        "client_version": context.get("clientVersion", ""),
        "locale": context.get("locale", ""),
    }
    logger.warning("Client stream telemetry", extra=log_payload)
    capture_message(
        f"client_stream:{data['event']}",
        level="warning",
        endpoint="chat_stream_v2_client",
        **log_payload,
    )
    return Response({"success": True})


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
