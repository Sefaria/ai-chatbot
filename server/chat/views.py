"""
Chat API views with routed Claude agent integration.

Implements the orchestrator pattern:
1. Receive user message
2. Load/generate conversation summary
3. Route to appropriate flow
4. Execute agent with flow-specific prompts/tools
5. Update summary and session state
6. Trace to Braintrust via native @traced decorator
"""

import asyncio
import contextvars
import json
import logging
import os
import queue
import time
from datetime import datetime
from threading import Thread
from urllib.parse import urlparse

import braintrust
from django.conf import settings
from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .agent import AgentProgressUpdate, ClaudeAgentService, ConversationMessage
from .metrics import TokenUsage
from .models import ChatMessage, ChatSession, RouteDecision
from .prompts import get_prompt_service
from .router import Flow, RouteResult, RouterService, get_router_service
from .serializers import (
    ChatRequestSerializer,
    HistoryMessageSerializer,
    OpenAIChatRequestSerializer,
)
from .summarization import ConversationSummary, get_summary_service
from .test_questions import get_test_response

logger = logging.getLogger("chat")


def extract_page_type(url: str) -> str:
    """
    Extract page type from Sefaria URL for Braintrust logging.

    Returns:
        - 'cauldron_<name>' for <name>.cauldron.sefaria.org (dev environments)
        - 'staging' for sefariastaging.org or sefariastaging-il.org
        - 'eval' etc. for subdomains like eval.sefaria.org
        - 'home' for /texts page
        - 'reader' for text pages like /Genesis.1
        - 'other' for root or misc pages
        - 'unknown' if no URL provided
    """
    if not url:
        return "unknown"

    parsed = urlparse(url)
    host = parsed.netloc.lower()

    # Check for cauldron dev environments (e.g., foo.cauldron.sefaria.org -> 'cauldron_foo')
    if ".cauldron." in host:
        name = host.split(".")[0]
        return f"cauldron_{name}"

    # Check for staging domains (sefariastaging.org, sefariastaging-il.org, www.sefariastaging.org)
    if "sefariastaging" in host:
        return "staging"

    # Check for subdomain on sefaria.org or sefaria.org.il (e.g., eval.sefaria.org -> 'eval')
    # Production domains: sefaria.org, sefaria.org.il, www.sefaria.org, www.sefaria.org.il
    if "sefaria.org" in host:
        # Remove www. prefix if present
        host_clean = host.replace("www.", "")
        # Check if there's a subdomain before sefaria.org
        if host_clean not in ("sefaria.org", "sefaria.org.il"):
            subdomain = host_clean.split(".")[0]
            return subdomain

    path = parsed.path.lower()

    if path in ("/texts", "/texts/"):
        return "home"
    if path and path != "/" and not path.startswith("/static"):
        return "reader"

    return "other"


def extract_page_context(context: dict) -> dict:
    """Extract page context from request context for Braintrust logging."""
    page_url = context.get("pageUrl", "")
    parsed_url = urlparse(page_url) if page_url else None
    client_version = context.get("clientVersion", "")
    return {
        "site": parsed_url.netloc if parsed_url else "",
        "page_type": extract_page_type(page_url),
        "page_url": page_url,
        "client_version": client_version,
        # Infer source: component always sends clientVersion, direct API calls typically don't
        "source": "component" if client_version else "api",
    }


def build_session_info(session) -> dict:
    """Build session info dict for API response."""
    turn_count = session.turn_count or 0
    max_turns = settings.MAX_TURNS
    return {
        "turnCount": turn_count,
        "maxTurns": max_turns,
        "limitReached": turn_count >= max_turns,
    }


# Global services (initialized lazily)
_agent_service: ClaudeAgentService | None = None
_router_service: RouterService | None = None
_bt_logger = None


def _get_bt_logger():
    """Get or create the Braintrust logger for request-level tracing."""
    global _bt_logger
    if _bt_logger is None:
        bt_api_key = os.environ.get("BRAINTRUST_API_KEY")
        bt_project = os.environ.get("BRAINTRUST_PROJECT", "On Site Agent")
        if bt_api_key:
            try:
                _bt_logger = braintrust.init_logger(
                    project=bt_project,
                    api_key=bt_api_key,
                )
            except Exception as e:
                logger.warning(f"Failed to initialize Braintrust logger in views: {e}")
    return _bt_logger


def get_agent_service() -> ClaudeAgentService:
    """Get or create the agent service singleton."""
    global _agent_service
    if _agent_service is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is required")
        _agent_service = ClaudeAgentService(
            api_key=api_key,
            prompt_service=get_prompt_service(),
        )
    return _agent_service


def get_router() -> RouterService:
    """Get or create the router service singleton."""
    global _router_service
    if _router_service is None:
        _router_service = get_router_service()
    return _router_service


def run_async(coro):
    """Run an async coroutine in a sync context, preserving Braintrust span context."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_running():
        import concurrent.futures

        # Capture current context (including Braintrust span) before switching threads
        ctx = contextvars.copy_context()

        def run_with_context():
            # Run asyncio.run inside the captured context
            return ctx.run(asyncio.run, coro)

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_with_context)
            return future.result()
    else:
        return loop.run_until_complete(coro)


def _update_session_after_response(
    session: ChatSession,
    agent_response,
    route_result: RouteResult,
    new_summary_text: str | None = None,
) -> None:
    """
    Update session state after an agent response.

    Args:
        session: The chat session to update
        agent_response: The agent's response containing token counts and tool calls
        route_result: The routing result for this turn
        new_summary_text: The updated conversation summary (if computed)
    """
    session.message_count = ChatMessage.objects.filter(session_id=session.session_id).count()
    session.turn_count = (session.turn_count or 0) + 1
    session.current_flow = route_result.flow.value
    session.total_input_tokens = (session.total_input_tokens or 0) + agent_response.input_tokens
    session.total_output_tokens = (session.total_output_tokens or 0) + agent_response.output_tokens
    session.total_tool_calls = (session.total_tool_calls or 0) + len(agent_response.tool_calls)

    update_fields = [
        "message_count",
        "turn_count",
        "last_activity",
        "current_flow",
        "total_input_tokens",
        "total_output_tokens",
        "total_tool_calls",
    ]

    if new_summary_text is not None:
        session.conversation_summary = new_summary_text
        session.summary_updated_at = timezone.now()
        update_fields.extend(["conversation_summary", "summary_updated_at"])

    session.save(update_fields=update_fields)


def _save_route_decision(
    route_result: RouteResult,
    session_id: str,
    turn_id: str,
    user_message: str,
    summary: str = "",
    previous_flow: str = "",
) -> RouteDecision:
    """Save a route decision to the database."""
    return RouteDecision.objects.create(
        decision_id=route_result.decision_id,
        session_id=session_id,
        turn_id=turn_id,
        user_message=user_message[:5000],
        conversation_summary_used=summary[:5000],
        previous_flow=previous_flow,
        flow=route_result.flow.value,
        confidence=route_result.confidence,
        reason_codes=[code.value for code in route_result.reason_codes],
        core_prompt_id=route_result.prompt_bundle.core_prompt_id,
        core_prompt_version=route_result.prompt_bundle.core_prompt_version,
        flow_prompt_id=route_result.prompt_bundle.flow_prompt_id,
        flow_prompt_version=route_result.prompt_bundle.flow_prompt_version,
        tools_attached=route_result.tools,
        session_action=route_result.session_action.value,
        safety_allowed=route_result.safety.allowed,
        refusal_message=route_result.safety.refusal_message or "",
        router_latency_ms=route_result.router_latency_ms,
    )


@api_view(["POST"])
def chat(request):
    """
    Handle incoming chat messages with routed Claude agent.

    POST /api/chat

    Orchestration flow:
    1. Validate request
    2. Load session and conversation summary
    3. Route message to appropriate flow
    4. Execute agent with flow-specific configuration
    5. Update summary and save response
    6. Return response with routing metadata
    """
    start_time = time.time()

    # Validate request
    serializer = ChatRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {"error": "Invalid request", "details": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    data = serializer.validated_data
    context = data.get("context", {})
    page_context = extract_page_context(context)
    environment = os.environ.get("ENVIRONMENT", "dev")

    # Generate turn ID
    turn_id = ChatMessage.generate_turn_id()

    # Log incoming message
    logger.info(
        f"📨 user={data['userId']} session={data['sessionId'][:20]}... "
        f"turn={turn_id[:20]}... text={data['text'][:50]}..."
    )

    # Update or create session
    session, session_created = ChatSession.objects.update_or_create(
        session_id=data["sessionId"],
        defaults={
            "user_id": data["userId"],
            "last_activity": timezone.now(),
        },
    )

    # Check turn limit
    if (session.turn_count or 0) >= settings.MAX_TURNS:
        return Response(
            {
                "error": "turn_limit_reached",
                "message": "Turn limit reached. Start a new conversation.",
                "maxTurns": settings.MAX_TURNS,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Check for test questions (Q1, Q2, Q3) - must be after turn limit check
    test_response = get_test_response(data["text"])
    if test_response:
        logger.info(f"🧪 Test question detected: {data['text']}")
        return Response(
            {
                "messageId": f"test_{data['text'].lower()}",
                "sessionId": data["sessionId"],
                "timestamp": timezone.now().isoformat(),
                "markdown": test_response["markdown"],
                "routing": {
                    "flow": test_response["flow"],
                    "decisionId": "test_decision",
                    "confidence": 1.0,
                    "wasRefused": False,
                },
                "session": build_session_info(session),
            }
        )

    # Get conversation summary for routing
    conversation_summary = session.conversation_summary or ""
    previous_flow = session.current_flow or ""

    # Initialize Braintrust logger for request-level span
    _get_bt_logger()

    # Start request-level span
    with braintrust.start_span(name="request", type="task") as request_span:
        # Log request input
        request_span.log(
            input={"query": data["text"]},
            metadata={
                "session_id": data["sessionId"],
                "user_id": data["userId"],
                "turn_id": turn_id,
                **page_context,
            },
            tags=[environment],
        )

        # Route the message
        router = get_router()
        route_result = router.route(
            session_id=data["sessionId"],
            user_message=data["text"],
            conversation_summary=conversation_summary,
            previous_flow=previous_flow if previous_flow else None,
            user_metadata={
                "locale": context.get("locale", ""),
                "pageUrl": context.get("pageUrl", ""),
            },
        )

        # Save route decision
        route_decision = _save_route_decision(
            route_result=route_result,
            session_id=data["sessionId"],
            turn_id=turn_id,
            user_message=data["text"],
            summary=conversation_summary,
            previous_flow=previous_flow,
        )

        logger.info(
            f"🔀 Route: flow={route_result.flow.value} confidence={route_result.confidence:.2f} "
            f"reasons={[c.value for c in route_result.reason_codes[:3]]}"
        )

        # Save user message
        user_message = ChatMessage.objects.create(
            message_id=data["messageId"],
            session_id=data["sessionId"],
            user_id=data["userId"],
            turn_id=turn_id,
            route_decision=route_decision,
            role=ChatMessage.Role.USER,
            content=data["text"],
            client_timestamp=data["timestamp"],
            page_url=context.get("pageUrl", ""),
            locale=context.get("locale", ""),
            client_version=context.get("clientVersion", ""),
            flow=route_result.flow.value,
        )

        try:
            # Get conversation history for context
            history_messages = ChatMessage.objects.filter(session_id=data["sessionId"]).order_by(
                "server_timestamp"
            )[:50]

            conversation = [
                ConversationMessage(role=msg.role, content=msg.content) for msg in history_messages
            ]

            # Execute agent with routing context
            agent = get_agent_service()
            agent_response = run_async(
                agent.send_message(
                    messages=conversation,
                    route_result=route_result,
                    session_id=data["sessionId"],
                    user_id=data["userId"],
                    turn_id=turn_id,
                    **page_context,
                )
            )

            # Calculate total latency
            latency_ms = int((time.time() - start_time) * 1000)

            # Save assistant response
            response_message = ChatMessage.objects.create(
                message_id=ChatMessage.generate_message_id(),
                session_id=data["sessionId"],
                user_id=data["userId"],
                turn_id=turn_id,
                route_decision=route_decision,
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
                flow=route_result.flow.value,
                status=ChatMessage.Status.REFUSED
                if agent_response.was_refused
                else ChatMessage.Status.SUCCESS,
            )

            # Link user message to response
            user_message.response_message = response_message
            user_message.latency_ms = latency_ms
            user_message.save(update_fields=["response_message", "latency_ms"])

            # Update conversation summary
            summary_service = get_summary_service()
            summary_result = run_async(
                summary_service.update_summary(
                    current_summary=ConversationSummary(text=conversation_summary)
                    if conversation_summary
                    else None,
                    new_user_message=data["text"],
                    new_assistant_response=agent_response.content,
                    flow=route_result.flow.value,
                )
            )

            # Update session with summary and stats
            _update_session_after_response(
                session=session,
                agent_response=agent_response,
                route_result=route_result,
                new_summary_text=summary_result.summary.to_text(),
            )

            # Log response
            logger.info(
                f"📤 response={response_message.message_id[:20]}... flow={route_result.flow.value} "
                f"latency={latency_ms}ms tools={len(agent_response.tool_calls)} "
                f"tokens={agent_response.input_tokens}+{agent_response.output_tokens}"
            )

            # Aggregate token usage from all LLM calls: router + agent + summary
            total_usage = TokenUsage(
                input_tokens=agent_response.input_tokens,
                output_tokens=agent_response.output_tokens,
                cache_creation_input_tokens=agent_response.cache_creation_tokens,
                cache_read_input_tokens=agent_response.cache_read_tokens,
            )

            # Add router tokens (guardrails + classifier)
            if route_result.token_usage:
                total_usage = total_usage + route_result.token_usage

            # Add summary tokens
            if summary_result.token_usage:
                total_usage = total_usage + summary_result.token_usage

            # Count all LLM calls: agent iterations + router (up to 2) + summary (1)
            # Router makes up to 2 calls: guardrails + classifier (if AI enabled)
            router_llm_calls = 2 if route_result.token_usage else 0
            summary_llm_calls = 1 if summary_result.token_usage else 0
            total_llm_calls = agent_response.llm_calls + router_llm_calls + summary_llm_calls

            # Log request output to span with Braintrust-compatible token metrics
            request_span.log(
                output={"response": agent_response.content[:500]},
                tags=[route_result.flow.value.lower()],
                metrics={
                    "latency_ms": latency_ms,
                    "llm_calls": total_llm_calls,
                    "tool_calls": len(agent_response.tool_calls),
                    **total_usage.to_braintrust(),
                },
            )

            # Reload session to get updated turn_count
            session.refresh_from_db()

            # Return response with routing metadata
            response_data = {
                "messageId": response_message.message_id,
                "sessionId": data["sessionId"],
                "timestamp": response_message.server_timestamp.isoformat(),
                "markdown": agent_response.content,
                "routing": {
                    "flow": route_result.flow.value,
                    "decisionId": route_result.decision_id,
                    "confidence": route_result.confidence,
                    "wasRefused": agent_response.was_refused,
                },
                "session": build_session_info(session),
            }

            return Response(response_data)

        except Exception as e:
            logger.error(f"❌ Agent error: {e}", exc_info=True)

            error_message = ChatMessage.objects.create(
                message_id=ChatMessage.generate_message_id(),
                session_id=data["sessionId"],
                user_id=data["userId"],
                turn_id=turn_id,
                route_decision=route_decision,
                role=ChatMessage.Role.ASSISTANT,
                content="I'm sorry, I encountered an error processing your request. Please try again.",
                status=ChatMessage.Status.FAILED,
                latency_ms=int((time.time() - start_time) * 1000),
                flow=route_result.flow.value,
            )

            user_message.response_message = error_message
            user_message.save(update_fields=["response_message"])

            # Log error to span
            request_span.log(
                output={"error": str(e)},
                error=str(e),
                metrics={"latency_ms": int((time.time() - start_time) * 1000)},
            )

            # Reload session to get current state
            session.refresh_from_db()

            return Response(
                {
                    "messageId": error_message.message_id,
                    "sessionId": data["sessionId"],
                    "timestamp": error_message.server_timestamp.isoformat(),
                    "markdown": error_message.content,
                    "routing": {
                        "flow": route_result.flow.value,
                        "decisionId": route_result.decision_id,
                        "wasRefused": False,
                    },
                    "session": build_session_info(session),
                }
            )


@api_view(["POST"])
def chat_stream(request):
    """
    Handle incoming chat messages with streaming progress via SSE.

    POST /api/chat/stream

    Response: Server-Sent Events stream with routing and tool progress.
    """
    start_time = time.time()

    serializer = ChatRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {"error": "Invalid request", "details": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    data = serializer.validated_data
    context = data.get("context", {})
    turn_id = ChatMessage.generate_turn_id()

    logger.info(
        f"📨 [stream] user={data['userId']} session={data['sessionId'][:20]}... "
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

    # Check turn limit
    if (session.turn_count or 0) >= settings.MAX_TURNS:
        return Response(
            {
                "error": "turn_limit_reached",
                "message": "Turn limit reached. Start a new conversation.",
                "maxTurns": settings.MAX_TURNS,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Check for test questions (Q1, Q2, Q3) - must be after turn limit check
    test_response = get_test_response(data["text"])
    if test_response:
        logger.info(f"🧪 [stream] Test question detected: {data['text']}")

        def generate_test_sse():
            """Generator that yields SSE events for test questions."""
            routing_event = {
                "type": "routing",
                "flow": test_response["flow"],
                "decisionId": "test_decision",
                "confidence": 1.0,
                "reasonCodes": ["TEST_QUESTION"],
            }
            yield f"event: routing\ndata: {json.dumps(routing_event)}\n\n"

            final_data = {
                "messageId": f"test_{data['text'].lower()}",
                "sessionId": data["sessionId"],
                "timestamp": timezone.now().isoformat(),
                "markdown": test_response["markdown"],
                "routing": {
                    "flow": test_response["flow"],
                    "decisionId": "test_decision",
                    "wasRefused": False,
                },
                "session": build_session_info(session),
                "stats": {
                    "llmCalls": 0,
                    "toolCalls": 0,
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "latencyMs": 0,
                },
            }
            yield f"event: message\ndata: {json.dumps(final_data)}\n\n"

        response = StreamingHttpResponse(generate_test_sse(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

    # Extract page context and environment for Braintrust logging
    page_context = extract_page_context(context)
    environment = os.environ.get("ENVIRONMENT", "dev")

    # Initialize Braintrust logger for request-level span
    _get_bt_logger()

    # Start request-level span for Braintrust tracing
    request_span = braintrust.start_span(name="stream-request", type="task")
    request_span.log(
        input={"query": data["text"]},
        metadata={
            "session_id": data["sessionId"],
            "user_id": data["userId"],
            "turn_id": turn_id,
            **page_context,
        },
        tags=[environment],
    )

    # Route the message
    router = get_router()
    route_result = router.route(
        session_id=data["sessionId"],
        user_message=data["text"],
        conversation_summary=session.conversation_summary or "",
        previous_flow=session.current_flow if session.current_flow else None,
        user_metadata={
            "locale": context.get("locale", ""),
            "pageUrl": context.get("pageUrl", ""),
        },
    )

    # Save route decision
    route_decision = _save_route_decision(
        route_result=route_result,
        session_id=data["sessionId"],
        turn_id=turn_id,
        user_message=data["text"],
        summary=session.conversation_summary or "",
        previous_flow=session.current_flow or "",
    )

    # Save user message
    user_message = ChatMessage.objects.create(
        message_id=data["messageId"],
        session_id=data["sessionId"],
        user_id=data["userId"],
        turn_id=turn_id,
        route_decision=route_decision,
        role=ChatMessage.Role.USER,
        content=data["text"],
        client_timestamp=data["timestamp"],
        page_url=context.get("pageUrl", ""),
        locale=context.get("locale", ""),
        client_version=context.get("clientVersion", ""),
        flow=route_result.flow.value,
    )

    def generate_sse():
        """Generator that yields SSE events."""
        progress_queue = queue.Queue()
        result_holder = {"response": None, "error": None}

        # Emit routing decision first
        routing_event = {
            "type": "routing",
            "flow": route_result.flow.value,
            "decisionId": route_result.decision_id,
            "confidence": route_result.confidence,
            "reasonCodes": [c.value for c in route_result.reason_codes[:5]],
        }
        yield f"event: routing\ndata: {json.dumps(routing_event)}\n\n"

        def on_progress(update: AgentProgressUpdate):
            progress_queue.put(update)

        # Capture current context (including Braintrust span) before switching threads
        ctx = contextvars.copy_context()

        def run_agent_with_context():
            """Run agent in captured context to preserve Braintrust span."""
            try:
                history_messages = ChatMessage.objects.filter(
                    session_id=data["sessionId"]
                ).order_by("server_timestamp")[:50]

                conversation = [
                    ConversationMessage(role=msg.role, content=msg.content)
                    for msg in history_messages
                ]

                agent = get_agent_service()
                result_holder["response"] = asyncio.run(
                    agent.send_message(
                        messages=conversation,
                        route_result=route_result,
                        on_progress=on_progress,
                        session_id=data["sessionId"],
                        user_id=data["userId"],
                        turn_id=turn_id,
                        **page_context,
                    )
                )
            except Exception as e:
                result_holder["error"] = str(e)
            finally:
                progress_queue.put(None)

        def run_agent():
            ctx.run(run_agent_with_context)

        agent_thread = Thread(target=run_agent, daemon=True)
        agent_thread.start()

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

        latency_ms = int((time.time() - start_time) * 1000)

        if result_holder["error"]:
            logger.error(f"❌ [stream] Agent error: {result_holder['error']}")

            error_msg = ChatMessage.objects.create(
                message_id=ChatMessage.generate_message_id(),
                session_id=data["sessionId"],
                user_id=data["userId"],
                turn_id=turn_id,
                route_decision=route_decision,
                role=ChatMessage.Role.ASSISTANT,
                content="I'm sorry, I encountered an error processing your request.",
                status=ChatMessage.Status.FAILED,
                latency_ms=latency_ms,
                flow=route_result.flow.value,
            )

            user_message.response_message = error_msg
            user_message.save(update_fields=["response_message"])

            # Close the Braintrust span with error
            try:
                request_span.log(
                    output={"error": result_holder["error"]},
                    error=result_holder["error"],
                    metrics={"latency_ms": latency_ms},
                )
            finally:
                request_span.end()

            yield f"event: error\ndata: {json.dumps({'error': result_holder['error']})}\n\n"
            return

        agent_response = result_holder["response"]

        # Save assistant response
        response_message = ChatMessage.objects.create(
            message_id=ChatMessage.generate_message_id(),
            session_id=data["sessionId"],
            user_id=data["userId"],
            turn_id=turn_id,
            route_decision=route_decision,
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
            flow=route_result.flow.value,
            status=ChatMessage.Status.REFUSED
            if agent_response.was_refused
            else ChatMessage.Status.SUCCESS,
        )

        user_message.response_message = response_message
        user_message.latency_ms = latency_ms
        user_message.save(update_fields=["response_message", "latency_ms"])

        # Update conversation summary (run in captured context to nest span under request)
        current_summary = session.conversation_summary or ""
        summary_service = get_summary_service()

        def run_summary():
            return asyncio.run(
                summary_service.update_summary(
                    current_summary=ConversationSummary(text=current_summary)
                    if current_summary
                    else None,
                    new_user_message=data["text"],
                    new_assistant_response=agent_response.content,
                    flow=route_result.flow.value,
                )
            )

        summary_result = ctx.run(run_summary)

        # Update session with summary and stats
        _update_session_after_response(
            session=session,
            agent_response=agent_response,
            route_result=route_result,
            new_summary_text=summary_result.summary.to_text(),
        )

        logger.info(
            f"📤 [stream] response={response_message.message_id[:20]}... flow={route_result.flow.value} "
            f"latency={latency_ms}ms tools={len(agent_response.tool_calls)}"
        )

        # Reload session to get updated turn_count
        session.refresh_from_db()

        final_data = {
            "messageId": response_message.message_id,
            "sessionId": data["sessionId"],
            "timestamp": response_message.server_timestamp.isoformat(),
            "markdown": agent_response.content,
            "routing": {
                "flow": route_result.flow.value,
                "decisionId": route_result.decision_id,
                "wasRefused": agent_response.was_refused,
            },
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

        # Close the Braintrust span with output
        try:
            # Aggregate token usage from all LLM calls: router + agent + summary
            total_usage = TokenUsage(
                input_tokens=agent_response.input_tokens,
                output_tokens=agent_response.output_tokens,
                cache_creation_input_tokens=agent_response.cache_creation_tokens,
                cache_read_input_tokens=agent_response.cache_read_tokens,
            )

            # Add router tokens (guardrails + classifier)
            if route_result.token_usage:
                total_usage = total_usage + route_result.token_usage

            # Add summary tokens
            if summary_result.token_usage:
                total_usage = total_usage + summary_result.token_usage

            # Count all LLM calls: agent iterations + router (up to 2) + summary (1)
            router_llm_calls = 2 if route_result.token_usage else 0
            summary_llm_calls = 1 if summary_result.token_usage else 0
            total_llm_calls = agent_response.llm_calls + router_llm_calls + summary_llm_calls

            request_span.log(
                output={"response": agent_response.content[:500]},
                tags=[route_result.flow.value.lower()],
                metrics={
                    "latency_ms": latency_ms,
                    "llm_calls": total_llm_calls,
                    "tool_calls": len(agent_response.tool_calls),
                    **total_usage.to_braintrust(),
                },
            )
        finally:
            request_span.end()

    response = StreamingHttpResponse(generate_sse(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"

    return response


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
                "braintrust": True,  # Native tracing always available
            },
        }
    )


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
    OpenAI-compatible chat completions endpoint for Braintrust integration.

    POST /api/v1/chat/completions

    Accepts OpenAI chat completion format, calls the Sefaria agent,
    and returns response in OpenAI format with routing metadata.
    """
    import uuid

    start_time = time.time()

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

    # Generate identifiers with bt- prefix for traceability
    session_id = f"bt-{uuid.uuid4().hex[:12]}"
    user_id = "bt-braintrust-playground"
    turn_id = ChatMessage.generate_turn_id()
    message_id = f"msg-{uuid.uuid4().hex[:12]}"

    logger.info(
        f"[openai-compat] user={user_id} session={session_id[:20]}... text={user_message[:50]}..."
    )

    # Create session
    session, _ = ChatSession.objects.update_or_create(
        session_id=session_id,
        defaults={
            "user_id": user_id,
            "last_activity": timezone.now(),
        },
    )

    # Set up Braintrust-specific context
    page_context = {
        "site": "braintrust.dev",
        "page_type": "playground",
        "page_url": "https://braintrust.dev/playground",
        "client_version": "openai-compat-1.0",
        "source": "braintrust",
    }

    # Initialize Braintrust logger
    _get_bt_logger()
    environment = os.environ.get("ENVIRONMENT", "dev")

    with braintrust.start_span(name="openai-compat-request", type="task") as request_span:
        request_span.log(
            input={"query": user_message},
            metadata={
                "session_id": session_id,
                "user_id": user_id,
                "turn_id": turn_id,
                **page_context,
            },
            tags=[environment, "braintrust", "openai-compat"],
        )

        # Route the message
        router = get_router()
        route_result = router.route(
            session_id=session_id,
            user_message=user_message,
            conversation_summary="",
            previous_flow=None,
            user_metadata={
                "locale": "",
                "pageUrl": page_context["page_url"],
            },
        )

        # Save route decision (same as regular endpoint)
        route_decision = _save_route_decision(
            route_result=route_result,
            session_id=session_id,
            turn_id=turn_id,
            user_message=user_message,
            summary="",
            previous_flow="",
        )

        logger.info(
            f"[openai-compat] Route: flow={route_result.flow.value} "
            f"confidence={route_result.confidence:.2f}"
        )

        # Save user message
        user_msg = ChatMessage.objects.create(
            message_id=message_id,
            session_id=session_id,
            user_id=user_id,
            turn_id=turn_id,
            route_decision=route_decision,
            role=ChatMessage.Role.USER,
            content=user_message,
            client_timestamp=timezone.now(),
            page_url=page_context["page_url"],
            locale="",
            client_version=page_context["client_version"],
            flow=route_result.flow.value,
        )

        # Handle refusals early - don't call agent for refused requests
        if route_result.flow == Flow.REFUSE:
            refusal_message = route_result.safety.refusal_message or "I can't process this request."
            latency_ms = int((time.time() - start_time) * 1000)

            # Save refusal response
            response_msg = ChatMessage.objects.create(
                message_id=ChatMessage.generate_message_id(),
                session_id=session_id,
                user_id=user_id,
                turn_id=turn_id,
                route_decision=route_decision,
                role=ChatMessage.Role.ASSISTANT,
                content=refusal_message,
                latency_ms=latency_ms,
                flow=route_result.flow.value,
                status=ChatMessage.Status.REFUSED,
            )

            # Link user message to response
            user_msg.response_message = response_msg
            user_msg.latency_ms = latency_ms
            user_msg.save(update_fields=["response_message", "latency_ms"])

            request_span.log(
                output={"response": refusal_message, "refused": True},
                tags=["refuse", "braintrust"],
                metrics={"latency_ms": latency_ms},
            )

            return Response(
                {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": data["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": refusal_message,
                            },
                            "finish_reason": "content_filter",
                        }
                    ],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "routing": {
                        "flow": route_result.flow.value,
                        "decision_id": route_result.decision_id,
                        "confidence": route_result.confidence,
                        "was_refused": True,
                    },
                }
            )

        try:
            # Build conversation from OpenAI messages
            conversation = [
                ConversationMessage(role=m["role"], content=m["content"]) for m in messages
            ]

            # Execute agent
            agent = get_agent_service()
            agent_response = run_async(
                agent.send_message(
                    messages=conversation,
                    route_result=route_result,
                    session_id=session_id,
                    user_id=user_id,
                    turn_id=turn_id,
                    **page_context,
                )
            )

            latency_ms = int((time.time() - start_time) * 1000)

            # Save assistant response
            response_msg = ChatMessage.objects.create(
                message_id=ChatMessage.generate_message_id(),
                session_id=session_id,
                user_id=user_id,
                turn_id=turn_id,
                route_decision=route_decision,
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
                flow=route_result.flow.value,
                status=ChatMessage.Status.REFUSED
                if agent_response.was_refused
                else ChatMessage.Status.SUCCESS,
            )

            # Link user message to response
            user_msg.response_message = response_msg
            user_msg.latency_ms = latency_ms
            user_msg.save(update_fields=["response_message", "latency_ms"])

            # Update session (no summary for single-turn playground use)
            _update_session_after_response(
                session=session,
                agent_response=agent_response,
                route_result=route_result,
                new_summary_text=None,  # OpenAI-compat is single-turn, no summary needed
            )

            logger.info(
                f"[openai-compat] response={response_msg.message_id[:20]}... "
                f"latency={latency_ms}ms tokens={agent_response.input_tokens}+{agent_response.output_tokens}"
            )

            # Aggregate token usage from router + agent (no summary for single-turn)
            total_usage = TokenUsage(
                input_tokens=agent_response.input_tokens,
                output_tokens=agent_response.output_tokens,
                cache_creation_input_tokens=agent_response.cache_creation_tokens,
                cache_read_input_tokens=agent_response.cache_read_tokens,
            )

            # Add router tokens (guardrails + classifier)
            if route_result.token_usage:
                total_usage = total_usage + route_result.token_usage

            # Count LLM calls: agent + router (no summary for single-turn)
            router_llm_calls = 2 if route_result.token_usage else 0
            total_llm_calls = agent_response.llm_calls + router_llm_calls

            request_span.log(
                output={"response": agent_response.content[:500]},
                tags=[route_result.flow.value.lower(), "braintrust"],
                metrics={
                    "latency_ms": latency_ms,
                    "llm_calls": total_llm_calls,
                    "tool_calls": len(agent_response.tool_calls),
                    **total_usage.to_braintrust(),
                },
            )

            # Return OpenAI-compatible response
            return Response(
                {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": data["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": agent_response.content,
                            },
                            "finish_reason": "content_filter"
                            if agent_response.was_refused
                            else "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": agent_response.input_tokens,
                        "completion_tokens": agent_response.output_tokens,
                        "total_tokens": agent_response.input_tokens + agent_response.output_tokens,
                    },
                    "routing": {
                        "flow": route_result.flow.value,
                        "decision_id": route_result.decision_id,
                        "confidence": route_result.confidence,
                        "was_refused": agent_response.was_refused,
                    },
                }
            )

        except Exception as e:
            logger.error(f"[openai-compat] Agent error: {e}", exc_info=True)

            request_span.log(
                output={"error": str(e)},
                error=str(e),
            )

            return _openai_error_response(
                message=f"Internal error: {e!s}",
                error_type="internal_error",
                code="agent_error",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
