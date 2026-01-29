"""
V2 chat API views with Claude agent integration.
"""

import asyncio
import concurrent.futures
import contextvars
import json
import logging
import os
import queue
import time
from datetime import datetime

from django.conf import settings
from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .agent import AgentProgressUpdate, ClaudeAgentService, ConversationMessage
from .summarization import get_summary_service
from ..models import ChatMessage, ChatSession, ConversationSummary
from ..serializers import ChatRequestSerializer, FeedbackRequestSerializer
from ..user_token_service import (
    UserTokenError,
    UserTokenExpiredError,
    decrypt_chatbot_user_token,
)

logger = logging.getLogger("chat")


def build_session_info(session) -> dict:
    """Build session info dict for API response."""
    turn_count = session.turn_count or 0
    return {
        "turnCount": turn_count,
    }


def _apply_page_context_to_user_message(message: str, page_url: str) -> str:
    """Append page URL context to the user message for the agent prompt."""
    if not page_url:
        return message
    return (
        f"{message}\n\n"
        f"User is currently on the Sefaria url: {page_url}. "
        "If the context is relevant, use that information in your response"
    )


def _create_traced_executor() -> concurrent.futures.Executor:
    """Create a ThreadPoolExecutor that preserves Braintrust context when available."""
    try:
        import braintrust

        traced_executor = getattr(braintrust, "TracedThreadPoolExecutor", None)
        if traced_executor:
            return traced_executor(max_workers=1)
    except Exception:
        pass

    return concurrent.futures.ThreadPoolExecutor(max_workers=1)


# Global services (initialized lazily)
_agent_service: ClaudeAgentService | None = None
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


def get_agent_service() -> ClaudeAgentService:
    """Get or create the agent service singleton."""
    global _agent_service
    if _agent_service is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is required")
        _agent_service = ClaudeAgentService(api_key=api_key)
    return _agent_service


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
    secret = settings.CHATBOT_USER_TOKEN_SECRET
    if not secret:
        logger.error("❌ [v2 stream] CHATBOT_USER_TOKEN_SECRET is not configured")
        return Response(
            {"error": "userId_decryption_unavailable"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    try:
        decrypted_user_id = decrypt_chatbot_user_token(data["userId"], secret)
    except UserTokenExpiredError:
        logger.warning("❌ [v2 stream] expired userId token")
        return Response({"error": "userId_expired"}, status=status.HTTP_401_UNAUTHORIZED)
    except UserTokenError as exc:
        logger.warning(f"❌ [v2 stream] invalid userId token: {exc}")
        return Response({"error": "invalid_userId"}, status=status.HTTP_401_UNAUTHORIZED)

    data["userId"] = decrypted_user_id
    context = data.get("context", {})
    page_url = context.get("pageUrl", "")
    prompt_slugs = data.get("promptSlugs") or {}
    turn_id = ChatMessage.generate_turn_id()

    logger.info(
        f"📨 [v2 stream] user={data['userId']} session={data['sessionId'][:20]}... "
        f"turn={turn_id[:20]}... text={data['text'][:50]}..."
    )

    # Update or create session
    session, _ = ChatSession.objects.update_or_create(
        session_id=data["sessionId"],
        defaults={
            "user_id": data["userId"],
            "last_activity": timezone.now(),
        },
    )

    # Load summary for this session (if any)
    summary = ConversationSummary.objects.filter(session=session).first()
    summary_text = summary.to_prompt_text() if summary else ""
    summary_metadata = summary.to_metadata() if summary else {}

    # Only core prompt slug is used for v2 streaming.
    core_prompt_slug = (prompt_slugs.get("corePromptSlug") or "").strip()
    if not core_prompt_slug:
        core_prompt_slug = settings.CORE_PROMPT_SLUG

    # Save user message
    user_message = ChatMessage.objects.create(
        message_id=data["messageId"],
        session_id=data["sessionId"],
        user_id=data["userId"],
        turn_id=turn_id,
        role=ChatMessage.Role.USER,
        content=data["text"],
        client_timestamp=data["timestamp"],
        locale=context.get("locale", ""),
        client_version=context.get("clientVersion", ""),
        flow="",
    )

    def generate_sse():
        """Generator that yields SSE events."""
        progress_queue = queue.Queue()
        result_holder = {"response": None, "error": None}

        def on_progress(update: AgentProgressUpdate):
            progress_queue.put(update)

        def run_agent():
            try:
                get_braintrust_logger()
                user_content = _apply_page_context_to_user_message(data["text"], page_url)
                conversation = [ConversationMessage(role="user", content=user_content)]
                agent = get_agent_service()
                result_holder["response"] = asyncio.run(
                    agent.send_message(
                        messages=conversation,
                        core_prompt_id=core_prompt_slug,
                        on_progress=on_progress,
                        session_id=data["sessionId"],
                        user_id=data["userId"],
                        turn_id=turn_id,
                        summary_text=summary_text,
                        summary_metadata=summary_metadata,
                        client_version=context.get("clientVersion", ""),
                    )
                )
            except Exception as e:
                result_holder["error"] = str(e)
            finally:
                progress_queue.put(None)

        ctx = contextvars.copy_context()
        executor = _create_traced_executor()
        future = executor.submit(ctx.run, run_agent)

        while True:
            try:
                update = progress_queue.get(timeout=60)

                if update is None:
                    break

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
                yield ": keepalive\n\n"

        try:
            future.result(timeout=5)
        except Exception:
            pass
        executor.shutdown(wait=False)

        latency_ms = int((time.time() - start_time) * 1000)

        if result_holder["error"]:
            logger.error(f"❌ [v2 stream] Agent error: {result_holder['error']}")

            error_msg = ChatMessage.objects.create(
                message_id=ChatMessage.generate_message_id(),
                session_id=data["sessionId"],
                user_id=data["userId"],
                turn_id=turn_id,
                role=ChatMessage.Role.ASSISTANT,
                content="I'm sorry, I encountered an error processing your request.",
                status=ChatMessage.Status.FAILED,
                latency_ms=latency_ms,
                flow="",
            )

            user_message.response_message = error_msg
            user_message.save(update_fields=["response_message"])

            yield f"event: error\ndata: {json.dumps({'error': result_holder['error']})}\n\n"
            return

        agent_response = result_holder["response"]

        # Save assistant response
        response_message = ChatMessage.objects.create(
            message_id=ChatMessage.generate_message_id(),
            session_id=data["sessionId"],
            user_id=data["userId"],
            turn_id=turn_id,
            role=ChatMessage.Role.ASSISTANT,
            content=agent_response.content,
            latency_ms=latency_ms,
            llm_calls=agent_response.llm_calls,
            tool_calls_count=len(agent_response.tool_calls),
            tool_calls_data=agent_response.tool_calls,
            input_tokens=agent_response.input_tokens,
            output_tokens=agent_response.output_tokens,
            cache_creation_tokens=agent_response.cache_creation_tokens,
            cache_read_tokens=agent_response.cache_read_tokens,
            model_name="claude-sonnet-4-5-20250929",
            flow="",
            status=ChatMessage.Status.SUCCESS,
        )

        user_message.response_message = response_message
        user_message.latency_ms = latency_ms
        user_message.save(update_fields=["response_message", "latency_ms"])

        # Update summary
        summary_service = get_summary_service()
        new_summary = summary_service.update_summary(
            session=session,
            new_user_message=data["text"],
            new_assistant_response=agent_response.content,
        )

        # Update session
        session.message_count = ChatMessage.objects.filter(session_id=data["sessionId"]).count()
        session.turn_count = (session.turn_count or 0) + 1
        session.current_flow = ""
        session.conversation_summary = new_summary.to_prompt_text()
        session.summary_updated_at = timezone.now()
        session.total_input_tokens = (session.total_input_tokens or 0) + agent_response.input_tokens
        session.total_output_tokens = (
            session.total_output_tokens or 0
        ) + agent_response.output_tokens
        session.total_tool_calls = (session.total_tool_calls or 0) + len(agent_response.tool_calls)
        session.save(
            update_fields=[
                "message_count",
                "turn_count",
                "last_activity",
                "current_flow",
                "conversation_summary",
                "summary_updated_at",
                "total_input_tokens",
                "total_output_tokens",
                "total_tool_calls",
            ]
        )

        logger.info(
            f"📤 [v2 stream] response={response_message.message_id[:20]}... "
            f"latency={latency_ms}ms tools={len(agent_response.tool_calls)}"
        )

        # Reload session to get updated turn_count
        session.refresh_from_db()

        final_data = {
            "messageId": response_message.message_id,
            "sessionId": data["sessionId"],
            "timestamp": response_message.server_timestamp.isoformat(),
            "markdown": agent_response.content,
            "traceId": agent_response.trace_id,
            "toolCalls": agent_response.tool_calls,
            "session": build_session_info(session),
            "stats": {
                "llmCalls": agent_response.llm_calls,
                "toolCalls": len(agent_response.tool_calls),
                "inputTokens": agent_response.input_tokens,
                "outputTokens": agent_response.output_tokens,
                "latencyMs": latency_ms,
            },
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
        return Response({"error": "feedback_log_failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return Response({"success": True})
