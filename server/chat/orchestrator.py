"""
Chat turn orchestration - unified logic for all endpoints.

Extracts the common flow from chat, chat_stream, and openai_chat_completions
into a single orchestration layer.
"""

import asyncio
import contextvars
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import braintrust
from django.conf import settings
from django.utils import timezone

from .agent import AgentProgressUpdate, AgentResponse, ClaudeAgentService, ConversationMessage
from .metrics import TokenUsage
from .models import ChatMessage, ChatSession, RouteDecision
from .prompts import get_prompt_service
from .router import RouteResult, RouterService, get_router_service
from .summarization import ConversationSummary, get_summary_service

logger = logging.getLogger("chat")


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------


@dataclass
class PageContext:
    """Context about the page/client making the request."""

    site: str = ""
    page_type: str = "unknown"
    page_url: str = ""
    client_version: str = ""
    source: str = "api"

    def to_dict(self) -> dict:
        return {
            "site": self.site,
            "page_type": self.page_type,
            "page_url": self.page_url,
            "client_version": self.client_version,
            "source": self.source,
        }


@dataclass
class TurnContext:
    """Everything needed to process a turn, created by prepare_turn()."""

    session: ChatSession
    route_result: RouteResult
    route_decision: RouteDecision
    user_message_record: ChatMessage
    conversation: list[ConversationMessage]
    turn_id: str
    user_id: str
    session_id: str
    page_context: PageContext
    start_time: float
    request_span: braintrust.Span
    environment: str = "dev"


@dataclass
class TurnResult:
    """Final result of a completed turn."""

    agent_response: AgentResponse
    route_result: RouteResult
    session: ChatSession
    response_message: ChatMessage
    latency_ms: int
    total_usage: TokenUsage
    total_llm_calls: int


@dataclass
class SessionInfo:
    """Session state for API responses."""

    turn_count: int
    max_turns: int
    limit_reached: bool

    @classmethod
    def from_session(cls, session: ChatSession) -> "SessionInfo":
        turn_count = session.turn_count or 0
        max_turns = settings.MAX_TURNS
        return cls(
            turn_count=turn_count,
            max_turns=max_turns,
            limit_reached=turn_count >= max_turns,
        )

    def to_dict(self) -> dict:
        return {
            "turnCount": self.turn_count,
            "maxTurns": self.max_turns,
            "limitReached": self.limit_reached,
        }


# ---------------------------------------------------------------------------
# Service Singletons
# ---------------------------------------------------------------------------

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
                logger.warning(f"Failed to initialize Braintrust logger: {e}")
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def extract_page_type(url: str) -> str:
    """Extract page type from Sefaria URL for Braintrust logging."""
    if not url:
        return "unknown"

    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if ".cauldron." in host:
        name = host.split(".")[0]
        return f"cauldron_{name}"

    if "sefariastaging" in host:
        return "staging"

    if "sefaria.org" in host:
        host_clean = host.replace("www.", "")
        if host_clean not in ("sefaria.org", "sefaria.org.il"):
            subdomain = host_clean.split(".")[0]
            return subdomain

    path = parsed.path.lower()

    if path in ("/texts", "/texts/"):
        return "home"
    if path and path != "/" and not path.startswith("/static"):
        return "reader"

    return "other"


def extract_page_context(context: dict) -> PageContext:
    """Extract page context from request context."""
    page_url = context.get("pageUrl", "")
    parsed_url = urlparse(page_url) if page_url else None
    client_version = context.get("clientVersion", "")
    return PageContext(
        site=parsed_url.netloc if parsed_url else "",
        page_type=extract_page_type(page_url),
        page_url=page_url,
        client_version=client_version,
        source="component" if client_version else "api",
    )


def run_async(coro):
    """Run an async coroutine in a sync context, preserving Braintrust span context."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_running():
        import concurrent.futures

        ctx = contextvars.copy_context()

        def run_with_context():
            return ctx.run(asyncio.run, coro)

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_with_context)
            return future.result()
    else:
        return loop.run_until_complete(coro)


def load_conversation_from_db(session_id: str, limit: int = 50) -> list[ConversationMessage]:
    """Load conversation history from database."""
    history_messages = ChatMessage.objects.filter(session_id=session_id).order_by(
        "server_timestamp"
    )[:limit]

    return [ConversationMessage(role=msg.role, content=msg.content) for msg in history_messages]


# ---------------------------------------------------------------------------
# Orchestration Functions
# ---------------------------------------------------------------------------


def prepare_turn(
    user_message: str,
    message_id: str,
    session_id: str,
    user_id: str,
    timestamp,
    context: dict,
    conversation: list[ConversationMessage],
    span_name: str = "request",
) -> TurnContext:
    """
    Prepare everything needed for agent execution.

    Handles: session management, routing, saving user message, Braintrust span setup.
    Returns TurnContext with everything needed to run the agent.
    """
    start_time = time.time()
    turn_id = ChatMessage.generate_turn_id()
    page_context = extract_page_context(context)
    environment = os.environ.get("ENVIRONMENT", "dev")

    logger.info(
        f"📨 user={user_id} session={session_id[:20]}... "
        f"turn={turn_id[:20]}... text={user_message[:50]}..."
    )

    # Create/update session
    session, _ = ChatSession.objects.update_or_create(
        session_id=session_id,
        defaults={
            "user_id": user_id,
            "last_activity": timezone.now(),
        },
    )

    # Initialize Braintrust logger
    _get_bt_logger()

    # Start request-level span
    request_span = braintrust.start_span(name=span_name, type="task")
    request_span.log(
        input={"query": user_message},
        metadata={
            "session_id": session_id,
            "user_id": user_id,
            "turn_id": turn_id,
            **page_context.to_dict(),
        },
        tags=[environment],
    )

    # Route the message
    conversation_summary = session.conversation_summary or ""
    previous_flow = session.current_flow or ""

    router = get_router()
    route_result = router.route(
        session_id=session_id,
        user_message=user_message,
        conversation_summary=conversation_summary,
        previous_flow=previous_flow if previous_flow else None,
        user_metadata={
            "locale": context.get("locale", ""),
            "pageUrl": context.get("pageUrl", ""),
        },
    )

    # Save route decision
    route_decision = RouteDecision.objects.create(
        decision_id=route_result.decision_id,
        session_id=session_id,
        turn_id=turn_id,
        user_message=user_message[:5000],
        conversation_summary_used=conversation_summary[:5000],
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

    logger.info(
        f"🔀 Route: flow={route_result.flow.value} confidence={route_result.confidence:.2f} "
        f"reasons={[c.value for c in route_result.reason_codes[:3]]}"
    )

    # Save user message
    user_message_record = ChatMessage.objects.create(
        message_id=message_id,
        session_id=session_id,
        user_id=user_id,
        turn_id=turn_id,
        route_decision=route_decision,
        role=ChatMessage.Role.USER,
        content=user_message,
        client_timestamp=timestamp,
        page_url=context.get("pageUrl", ""),
        locale=context.get("locale", ""),
        client_version=context.get("clientVersion", ""),
        flow=route_result.flow.value,
    )

    return TurnContext(
        session=session,
        route_result=route_result,
        route_decision=route_decision,
        user_message_record=user_message_record,
        conversation=conversation,
        turn_id=turn_id,
        user_id=user_id,
        session_id=session_id,
        page_context=page_context,
        start_time=start_time,
        request_span=request_span,
        environment=environment,
    )


def execute_agent(
    ctx: TurnContext,
    on_progress: Callable[[AgentProgressUpdate], None] | None = None,
) -> AgentResponse:
    """
    Execute the agent with the prepared context.

    Args:
        ctx: The prepared turn context
        on_progress: Optional callback for streaming progress updates
    """
    agent = get_agent_service()
    return run_async(
        agent.send_message(
            messages=ctx.conversation,
            route_result=ctx.route_result,
            on_progress=on_progress,
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            turn_id=ctx.turn_id,
            **ctx.page_context.to_dict(),
        )
    )


def complete_turn(
    ctx: TurnContext,
    agent_response: AgentResponse,
    include_summary: bool = True,
) -> TurnResult:
    """
    Complete the turn after agent execution.

    Handles: saving response, updating session, summary, Braintrust logging.
    """
    latency_ms = int((time.time() - ctx.start_time) * 1000)

    # Save assistant response
    response_message = ChatMessage.objects.create(
        message_id=ChatMessage.generate_message_id(),
        session_id=ctx.session_id,
        user_id=ctx.user_id,
        turn_id=ctx.turn_id,
        route_decision=ctx.route_decision,
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
        flow=ctx.route_result.flow.value,
        status=ChatMessage.Status.REFUSED
        if agent_response.was_refused
        else ChatMessage.Status.SUCCESS,
    )

    # Link user message to response
    ctx.user_message_record.response_message = response_message
    ctx.user_message_record.latency_ms = latency_ms
    ctx.user_message_record.save(update_fields=["response_message", "latency_ms"])

    # Update conversation summary
    summary_token_usage = None
    new_summary_text = None

    if include_summary:
        current_summary = ctx.session.conversation_summary or ""
        summary_service = get_summary_service()
        summary_result = run_async(
            summary_service.update_summary(
                current_summary=ConversationSummary(text=current_summary)
                if current_summary
                else None,
                new_user_message=ctx.user_message_record.content,
                new_assistant_response=agent_response.content,
                flow=ctx.route_result.flow.value,
            )
        )
        new_summary_text = summary_result.summary.to_text()
        summary_token_usage = summary_result.token_usage

    # Update session
    _update_session_after_response(
        session=ctx.session,
        agent_response=agent_response,
        route_result=ctx.route_result,
        new_summary_text=new_summary_text,
    )

    logger.info(
        f"📤 response={response_message.message_id[:20]}... flow={ctx.route_result.flow.value} "
        f"latency={latency_ms}ms tools={len(agent_response.tool_calls)} "
        f"tokens={agent_response.input_tokens}+{agent_response.output_tokens}"
    )

    # Aggregate token usage
    total_usage = TokenUsage(
        input_tokens=agent_response.input_tokens,
        output_tokens=agent_response.output_tokens,
        cache_creation_input_tokens=agent_response.cache_creation_tokens,
        cache_read_input_tokens=agent_response.cache_read_tokens,
    )

    if ctx.route_result.token_usage:
        total_usage = total_usage + ctx.route_result.token_usage

    if summary_token_usage:
        total_usage = total_usage + summary_token_usage

    # Count all LLM calls
    router_llm_calls = 2 if ctx.route_result.token_usage else 0
    summary_llm_calls = 1 if summary_token_usage else 0
    total_llm_calls = agent_response.llm_calls + router_llm_calls + summary_llm_calls

    # Log to Braintrust span
    ctx.request_span.log(
        output={"response": agent_response.content[:500]},
        tags=[ctx.route_result.flow.value.lower()],
        metrics={
            "latency_ms": latency_ms,
            "llm_calls": total_llm_calls,
            "tool_calls": len(agent_response.tool_calls),
            **total_usage.to_braintrust(),
        },
    )

    # Reload session for updated turn_count
    ctx.session.refresh_from_db()

    return TurnResult(
        agent_response=agent_response,
        route_result=ctx.route_result,
        session=ctx.session,
        response_message=response_message,
        latency_ms=latency_ms,
        total_usage=total_usage,
        total_llm_calls=total_llm_calls,
    )


def complete_turn_with_error(
    ctx: TurnContext,
    error: Exception,
) -> ChatMessage:
    """Handle turn completion when an error occurred."""
    latency_ms = int((time.time() - ctx.start_time) * 1000)

    logger.error(f"❌ Agent error: {error}", exc_info=True)

    error_message = ChatMessage.objects.create(
        message_id=ChatMessage.generate_message_id(),
        session_id=ctx.session_id,
        user_id=ctx.user_id,
        turn_id=ctx.turn_id,
        route_decision=ctx.route_decision,
        role=ChatMessage.Role.ASSISTANT,
        content="I'm sorry, I encountered an error processing your request. Please try again.",
        status=ChatMessage.Status.FAILED,
        latency_ms=latency_ms,
        flow=ctx.route_result.flow.value,
    )

    ctx.user_message_record.response_message = error_message
    ctx.user_message_record.save(update_fields=["response_message"])

    # Log error to span
    ctx.request_span.log(
        output={"error": str(error)},
        error=str(error),
        metrics={"latency_ms": latency_ms},
    )

    # Reload session
    ctx.session.refresh_from_db()

    return error_message


def _update_session_after_response(
    session: ChatSession,
    agent_response: AgentResponse,
    route_result: RouteResult,
    new_summary_text: str | None = None,
) -> None:
    """Update session state after an agent response."""
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


# ---------------------------------------------------------------------------
# Turn Limit Check
# ---------------------------------------------------------------------------


class TurnLimitReached(Exception):
    """Raised when session has reached max turns."""

    def __init__(self, max_turns: int):
        self.max_turns = max_turns
        super().__init__(f"Turn limit reached: {max_turns}")


def check_turn_limit(session: ChatSession) -> None:
    """Raise TurnLimitReached if session is at limit."""
    if (session.turn_count or 0) >= settings.MAX_TURNS:
        raise TurnLimitReached(settings.MAX_TURNS)
