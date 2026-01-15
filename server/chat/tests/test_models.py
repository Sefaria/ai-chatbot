"""
Tests for Django models - ChatSession, ChatMessage, RouteDecision, etc.
"""

import pytest
from django.utils import timezone
from datetime import timedelta

from chat.models import (
    ChatSession,
    ChatMessage,
    RouteDecision,
    ToolCallEvent,
    BraintrustLog,
)


@pytest.mark.django_db
class TestChatSession:
    """Test ChatSession model."""

    def test_create_session(self):
        """Test basic session creation."""
        session = ChatSession.objects.create(
            session_id="sess_test123",
            user_id="user_abc",
        )
        assert session.session_id == "sess_test123"
        assert session.user_id == "user_abc"
        assert session.message_count == 0
        assert session.turn_count == 0
        assert session.current_flow == ""

    def test_session_defaults(self):
        """Test default values."""
        session = ChatSession.objects.create(
            session_id="sess_defaults",
            user_id="user_defaults",
        )
        assert session.total_input_tokens == 0
        assert session.total_output_tokens == 0
        assert session.total_tool_calls == 0
        assert session.conversation_summary == ""
        assert session.user_locale == ""

    def test_session_with_flow(self):
        """Test session with flow set."""
        session = ChatSession.objects.create(
            session_id="sess_flow",
            user_id="user_flow",
            current_flow="HALACHIC",
        )
        assert session.current_flow == "HALACHIC"

    def test_session_with_summary(self):
        """Test session with conversation summary."""
        session = ChatSession.objects.create(
            session_id="sess_summary",
            user_id="user_summary",
            conversation_summary="User asked about Shabbat laws.",
        )
        assert session.conversation_summary == "User asked about Shabbat laws."

    def test_session_str(self):
        """Test string representation."""
        session = ChatSession.objects.create(
            session_id="sess_str",
            user_id="user_str",
            current_flow="SEARCH",
        )
        str_repr = str(session)
        assert "sess_str" in str_repr
        assert "user_str" in str_repr
        assert "SEARCH" in str_repr

    def test_session_unique_id(self):
        """Test session ID uniqueness."""
        ChatSession.objects.create(session_id="unique_id", user_id="user1")
        with pytest.raises(Exception):
            ChatSession.objects.create(session_id="unique_id", user_id="user2")

    def test_session_auto_timestamps(self):
        """Test auto-generated timestamps."""
        session = ChatSession.objects.create(
            session_id="sess_time",
            user_id="user_time",
        )
        assert session.created_at is not None
        assert session.last_activity is not None

    def test_session_ordering(self):
        """Test session ordering by last_activity."""
        old_session = ChatSession.objects.create(
            session_id="sess_old",
            user_id="user",
        )
        # Force update older timestamp
        old_session.last_activity = timezone.now() - timedelta(hours=1)
        old_session.save()

        new_session = ChatSession.objects.create(
            session_id="sess_new",
            user_id="user",
        )

        sessions = list(ChatSession.objects.all())
        assert sessions[0].session_id == "sess_new"


@pytest.mark.django_db
class TestRouteDecision:
    """Test RouteDecision model."""

    def test_create_route_decision(self):
        """Test basic route decision creation."""
        decision = RouteDecision.objects.create(
            decision_id="dec_test123",
            session_id="sess_123",
            turn_id="turn_456",
            user_message="Is this kosher?",
            flow="HALACHIC",
            confidence=0.85,
        )
        assert decision.decision_id == "dec_test123"
        assert decision.flow == "HALACHIC"
        assert decision.confidence == 0.85

    def test_route_decision_defaults(self):
        """Test default values."""
        decision = RouteDecision.objects.create(
            decision_id="dec_defaults",
            session_id="sess",
            turn_id="turn",
            user_message="test",
            flow="GENERAL",
        )
        assert decision.reason_codes == []
        assert decision.tools_attached == []
        assert decision.safety_allowed is True
        assert decision.refusal_message == ""
        assert decision.session_action == "CONTINUE"

    def test_route_decision_with_reason_codes(self):
        """Test with reason codes."""
        decision = RouteDecision.objects.create(
            decision_id="dec_reasons",
            session_id="sess",
            turn_id="turn",
            user_message="test",
            flow="HALACHIC",
            reason_codes=["ROUTE_HALACHIC_KEYWORDS", "ROUTE_HALACHIC_INTENT"],
        )
        assert len(decision.reason_codes) == 2
        assert "ROUTE_HALACHIC_KEYWORDS" in decision.reason_codes

    def test_route_decision_with_tools(self):
        """Test with tools attached."""
        decision = RouteDecision.objects.create(
            decision_id="dec_tools",
            session_id="sess",
            turn_id="turn",
            user_message="test",
            flow="SEARCH",
            tools_attached=["get_text", "text_search", "english_semantic_search"],
        )
        assert len(decision.tools_attached) == 3
        assert "get_text" in decision.tools_attached

    def test_route_decision_refuse_flow(self):
        """Test refuse flow decision."""
        decision = RouteDecision.objects.create(
            decision_id="dec_refuse",
            session_id="sess",
            turn_id="turn",
            user_message="bad message",
            flow="REFUSE",
            confidence=1.0,
            safety_allowed=False,
            refusal_message="Cannot process this request.",
            session_action="END",
        )
        assert decision.flow == "REFUSE"
        assert decision.safety_allowed is False
        assert decision.session_action == "END"

    def test_generate_decision_id(self):
        """Test decision ID generation."""
        decision_id = RouteDecision.generate_decision_id()
        assert decision_id.startswith("dec_")
        assert len(decision_id) == 20  # dec_ + 16 hex chars

    def test_route_decision_str(self):
        """Test string representation."""
        decision = RouteDecision.objects.create(
            decision_id="dec_str",
            session_id="sess",
            turn_id="turn",
            user_message="test",
            flow="GENERAL",
            reason_codes=["ROUTE_GENERAL_LEARNING", "TOOLS_MINIMAL_GENERAL_SET"],
        )
        str_repr = str(decision)
        assert "dec_str" in str_repr
        assert "GENERAL" in str_repr


@pytest.mark.django_db
class TestChatMessage:
    """Test ChatMessage model."""

    def test_create_user_message(self):
        """Test user message creation."""
        message = ChatMessage.objects.create(
            message_id="msg_user123",
            session_id="sess_123",
            user_id="user_abc",
            role="user",
            content="What is the halacha about this?",
        )
        assert message.message_id == "msg_user123"
        assert message.role == "user"
        assert message.content == "What is the halacha about this?"

    def test_create_assistant_message(self):
        """Test assistant message creation."""
        message = ChatMessage.objects.create(
            message_id="msg_assistant123",
            session_id="sess_123",
            user_id="user_abc",
            role="assistant",
            content="According to halacha...",
            model_name="claude-sonnet-4-5-20250929",
            input_tokens=100,
            output_tokens=200,
        )
        assert message.role == "assistant"
        assert message.model_name == "claude-sonnet-4-5-20250929"
        assert message.input_tokens == 100

    def test_message_defaults(self):
        """Test default values."""
        message = ChatMessage.objects.create(
            message_id="msg_defaults",
            session_id="sess",
            user_id="user",
            role="user",
            content="test",
        )
        assert message.status == "success"
        assert message.flow == ""
        assert message.tool_calls_count is None

    def test_message_with_flow(self):
        """Test message with flow context."""
        message = ChatMessage.objects.create(
            message_id="msg_flow",
            session_id="sess",
            user_id="user",
            role="user",
            content="test",
            flow="HALACHIC",
        )
        assert message.flow == "HALACHIC"

    def test_message_with_tool_calls(self):
        """Test assistant message with tool calls."""
        message = ChatMessage.objects.create(
            message_id="msg_tools",
            session_id="sess",
            user_id="user",
            role="assistant",
            content="Here is what I found...",
            tool_calls_count=3,
            tool_calls_data=[
                {"tool": "get_text", "input": {"reference": "Genesis 1:1"}},
                {"tool": "text_search", "input": {"query": "creation"}},
            ],
        )
        assert message.tool_calls_count == 3
        assert len(message.tool_calls_data) == 2

    def test_message_with_cache_tokens(self):
        """Test message with cache token tracking."""
        message = ChatMessage.objects.create(
            message_id="msg_cache",
            session_id="sess",
            user_id="user",
            role="assistant",
            content="response",
            input_tokens=500,
            output_tokens=100,
            cache_creation_tokens=400,
            cache_read_tokens=200,
        )
        assert message.cache_creation_tokens == 400
        assert message.cache_read_tokens == 200

    def test_message_status_choices(self):
        """Test message status choices."""
        for status in ["success", "failed", "refused"]:
            message = ChatMessage.objects.create(
                message_id=f"msg_{status}",
                session_id="sess",
                user_id="user",
                role="assistant",
                content="test",
                status=status,
            )
            assert message.status == status

    def test_generate_message_id(self):
        """Test message ID generation."""
        message_id = ChatMessage.generate_message_id()
        assert message_id.startswith("msg_")
        assert len(message_id) == 20

    def test_generate_turn_id(self):
        """Test turn ID generation."""
        turn_id = ChatMessage.generate_turn_id()
        assert turn_id.startswith("turn_")
        assert len(turn_id) == 21

    def test_message_str(self):
        """Test string representation."""
        message = ChatMessage.objects.create(
            message_id="msg_str",
            session_id="sess",
            user_id="user",
            role="user",
            content="This is a test message with more than 50 characters to check truncation",
        )
        str_repr = str(message)
        assert "user" in str_repr
        assert "..." in str_repr

    def test_message_ordering(self):
        """Test message ordering by timestamp."""
        msg1 = ChatMessage.objects.create(
            message_id="msg_1",
            session_id="sess",
            user_id="user",
            role="user",
            content="first",
        )
        msg2 = ChatMessage.objects.create(
            message_id="msg_2",
            session_id="sess",
            user_id="user",
            role="assistant",
            content="second",
        )
        messages = list(ChatMessage.objects.filter(session_id="sess"))
        assert messages[0].message_id == "msg_1"
        assert messages[1].message_id == "msg_2"


@pytest.mark.django_db
class TestMessageRouteDecisionRelation:
    """Test ChatMessage to RouteDecision relationship."""

    def test_message_with_route_decision(self):
        """Test message linked to route decision."""
        decision = RouteDecision.objects.create(
            decision_id="dec_rel",
            session_id="sess",
            turn_id="turn",
            user_message="test",
            flow="HALACHIC",
        )
        message = ChatMessage.objects.create(
            message_id="msg_rel",
            session_id="sess",
            user_id="user",
            role="user",
            content="test",
            route_decision=decision,
        )
        assert message.route_decision == decision
        assert message in decision.messages.all()

    def test_message_without_route_decision(self):
        """Test message without route decision."""
        message = ChatMessage.objects.create(
            message_id="msg_no_rel",
            session_id="sess",
            user_id="user",
            role="user",
            content="test",
        )
        assert message.route_decision is None


@pytest.mark.django_db
class TestToolCallEvent:
    """Test ToolCallEvent model."""

    def test_create_tool_call_event(self):
        """Test basic tool call event creation."""
        event = ToolCallEvent.objects.create(
            event_id="evt_test123",
            session_id="sess_123",
            turn_id="turn_456",
            tool_name="get_text",
            tool_input={"reference": "Genesis 1:1"},
            start_timestamp=timezone.now(),
        )
        assert event.event_id == "evt_test123"
        assert event.tool_name == "get_text"
        assert event.tool_input["reference"] == "Genesis 1:1"

    def test_tool_call_with_output(self):
        """Test tool call with output."""
        event = ToolCallEvent.objects.create(
            event_id="evt_output",
            session_id="sess",
            turn_id="turn",
            tool_name="text_search",
            tool_input={"query": "shabbat"},
            tool_output={"results": [{"ref": "Exodus 20:8"}]},
            start_timestamp=timezone.now(),
            end_timestamp=timezone.now(),
            latency_ms=150,
            success=True,
        )
        assert event.tool_output is not None
        assert event.latency_ms == 150
        assert event.success is True

    def test_tool_call_error(self):
        """Test tool call with error."""
        event = ToolCallEvent.objects.create(
            event_id="evt_error",
            session_id="sess",
            turn_id="turn",
            tool_name="get_text",
            tool_input={"reference": "Invalid Reference"},
            start_timestamp=timezone.now(),
            success=False,
            error_message="Reference not found",
            error_type="NotFoundError",
        )
        assert event.success is False
        assert event.error_message == "Reference not found"

    def test_generate_event_id(self):
        """Test event ID generation."""
        event_id = ToolCallEvent.generate_event_id()
        assert event_id.startswith("evt_")
        assert len(event_id) == 20

    def test_tool_call_str(self):
        """Test string representation."""
        event = ToolCallEvent.objects.create(
            event_id="evt_str",
            session_id="sess",
            turn_id="turn",
            tool_name="get_text",
            tool_input={},
            start_timestamp=timezone.now(),
            latency_ms=100,
            success=True,
        )
        str_repr = str(event)
        assert "get_text" in str_repr
        assert "100ms" in str_repr
        assert "✓" in str_repr


@pytest.mark.django_db
class TestBraintrustLog:
    """Test BraintrustLog model."""

    def test_create_braintrust_log(self):
        """Test basic log creation."""
        log = BraintrustLog.objects.create(
            log_id="log_test123",
            session_id="sess_123",
            turn_id="turn_456",
            user_message="What is kosher?",
            flow="HALACHIC",
        )
        assert log.log_id == "log_test123"
        assert log.flow == "HALACHIC"

    def test_braintrust_log_with_metrics(self):
        """Test log with full metrics."""
        log = BraintrustLog.objects.create(
            log_id="log_metrics",
            session_id="sess",
            turn_id="turn",
            user_message="test",
            flow="SEARCH",
            assistant_response="Here are the results...",
            latency_ms=2500,
            llm_calls=3,
            tool_calls_count=5,
            input_tokens=1000,
            output_tokens=500,
            estimated_cost_usd=0.05,
        )
        assert log.latency_ms == 2500
        assert log.llm_calls == 3
        assert log.estimated_cost_usd == 0.05

    def test_braintrust_log_with_tools(self):
        """Test log with tool info."""
        log = BraintrustLog.objects.create(
            log_id="log_tools",
            session_id="sess",
            turn_id="turn",
            user_message="test",
            flow="SEARCH",
            tools_available=["get_text", "text_search", "english_semantic_search"],
            tools_used=["text_search"],
        )
        assert len(log.tools_available) == 3
        assert len(log.tools_used) == 1

    def test_braintrust_log_refused(self):
        """Test refused log entry."""
        log = BraintrustLog.objects.create(
            log_id="log_refused",
            session_id="sess",
            turn_id="turn",
            user_message="bad message",
            flow="REFUSE",
            was_refused=True,
            refusal_reason_codes=["GUARDRAIL_PROMPT_INJECTION"],
        )
        assert log.was_refused is True
        assert "GUARDRAIL_PROMPT_INJECTION" in log.refusal_reason_codes

    def test_generate_log_id(self):
        """Test log ID generation."""
        log_id = BraintrustLog.generate_log_id()
        assert log_id.startswith("log_")
        assert len(log_id) == 20

    def test_braintrust_log_str(self):
        """Test string representation."""
        log = BraintrustLog.objects.create(
            log_id="log_str",
            session_id="sess",
            turn_id="turn",
            user_message="test",
            flow="GENERAL",
        )
        str_repr = str(log)
        assert "log_str" in str_repr
        assert "GENERAL" in str_repr
