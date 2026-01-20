"""Tests for Django models - ChatSession, ChatMessage, RouteDecision."""

from datetime import timedelta

import pytest
from django.utils import timezone

from chat.models import (
    ChatMessage,
    ChatSession,
    RouteDecision,
)


@pytest.mark.django_db
class TestChatSession:
    """Test ChatSession model."""

    def test_create_session(self) -> None:
        session = ChatSession.objects.create(session_id="sess_test123", user_id="user_abc")
        assert session.session_id == "sess_test123"
        assert session.user_id == "user_abc"
        assert session.message_count == 0
        assert session.turn_count == 0
        assert session.current_flow == ""

    def test_session_defaults(self) -> None:
        session = ChatSession.objects.create(session_id="sess_defaults", user_id="user")
        assert session.total_input_tokens == 0
        assert session.total_output_tokens == 0
        assert session.total_tool_calls == 0
        assert session.conversation_summary == ""
        assert session.user_locale == ""

    def test_session_with_flow_and_summary(self) -> None:
        session = ChatSession.objects.create(
            session_id="sess_flow",
            user_id="user",
            current_flow="HALACHIC",
            conversation_summary="User asked about Shabbat laws.",
        )
        assert session.current_flow == "HALACHIC"
        assert session.conversation_summary == "User asked about Shabbat laws."

    def test_session_str(self) -> None:
        session = ChatSession.objects.create(
            session_id="sess_str", user_id="user_str", current_flow="SEARCH"
        )
        str_repr = str(session)
        assert "sess_str" in str_repr
        assert "user_str" in str_repr
        assert "SEARCH" in str_repr

    def test_session_unique_id(self) -> None:
        from django.db import IntegrityError

        ChatSession.objects.create(session_id="unique_id", user_id="user1")
        with pytest.raises(IntegrityError):
            ChatSession.objects.create(session_id="unique_id", user_id="user2")

    def test_session_auto_timestamps(self) -> None:
        session = ChatSession.objects.create(session_id="sess_time", user_id="user")
        assert session.created_at is not None
        assert session.last_activity is not None

    def test_session_ordering(self) -> None:
        old_session = ChatSession.objects.create(session_id="sess_old", user_id="user")
        old_session.last_activity = timezone.now() - timedelta(hours=1)
        old_session.save()
        ChatSession.objects.create(session_id="sess_new", user_id="user")

        sessions = list(ChatSession.objects.all())
        assert sessions[0].session_id == "sess_new"


@pytest.mark.django_db
class TestRouteDecision:
    """Test RouteDecision model."""

    def test_create_route_decision(self) -> None:
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

    def test_route_decision_defaults(self) -> None:
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

    def test_route_decision_with_reason_codes_and_tools(self) -> None:
        decision = RouteDecision.objects.create(
            decision_id="dec_full",
            session_id="sess",
            turn_id="turn",
            user_message="test",
            flow="SEARCH",
            reason_codes=["ROUTE_HALACHIC_KEYWORDS", "ROUTE_HALACHIC_INTENT"],
            tools_attached=["get_text", "text_search", "english_semantic_search"],
        )
        assert len(decision.reason_codes) == 2
        assert "ROUTE_HALACHIC_KEYWORDS" in decision.reason_codes
        assert len(decision.tools_attached) == 3
        assert "get_text" in decision.tools_attached

    def test_route_decision_refuse_flow(self) -> None:
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

    def test_generate_decision_id(self) -> None:
        decision_id = RouteDecision.generate_decision_id()
        assert decision_id.startswith("dec_")
        assert len(decision_id) == 20

    def test_route_decision_str(self) -> None:
        decision = RouteDecision.objects.create(
            decision_id="dec_str",
            session_id="sess",
            turn_id="turn",
            user_message="test",
            flow="GENERAL",
            reason_codes=["ROUTE_GENERAL_LEARNING"],
        )
        str_repr = str(decision)
        assert "dec_str" in str_repr
        assert "GENERAL" in str_repr


@pytest.mark.django_db
class TestChatMessage:
    """Test ChatMessage model."""

    def test_create_user_message(self) -> None:
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

    def test_create_assistant_message(self) -> None:
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

    def test_message_defaults(self) -> None:
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

    def test_message_with_tool_calls(self) -> None:
        message = ChatMessage.objects.create(
            message_id="msg_tools",
            session_id="sess",
            user_id="user",
            role="assistant",
            content="Here is what I found...",
            flow="HALACHIC",
            tool_calls_count=3,
            tool_calls_data=[
                {"tool": "get_text", "input": {"reference": "Genesis 1:1"}},
                {"tool": "text_search", "input": {"query": "creation"}},
            ],
        )
        assert message.flow == "HALACHIC"
        assert message.tool_calls_count == 3
        assert len(message.tool_calls_data) == 2

    def test_message_with_cache_tokens(self) -> None:
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

    @pytest.mark.parametrize("status", ["success", "failed", "refused"])
    def test_message_status_choices(self, status: str) -> None:
        message = ChatMessage.objects.create(
            message_id=f"msg_{status}",
            session_id="sess",
            user_id="user",
            role="assistant",
            content="test",
            status=status,
        )
        assert message.status == status

    def test_generate_message_id(self) -> None:
        message_id = ChatMessage.generate_message_id()
        assert message_id.startswith("msg_")
        assert len(message_id) == 20

    def test_generate_turn_id(self) -> None:
        turn_id = ChatMessage.generate_turn_id()
        assert turn_id.startswith("turn_")
        assert len(turn_id) == 21

    def test_message_str(self) -> None:
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

    def test_message_ordering(self) -> None:
        ChatMessage.objects.create(
            message_id="msg_1", session_id="sess", user_id="user", role="user", content="first"
        )
        ChatMessage.objects.create(
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

    def test_message_with_route_decision(self) -> None:
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

    def test_message_without_route_decision(self) -> None:
        message = ChatMessage.objects.create(
            message_id="msg_no_rel",
            session_id="sess",
            user_id="user",
            role="user",
            content="test",
        )
        assert message.route_decision is None
