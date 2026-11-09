"""Persisting narration rounds alongside the final response (see ChatMessage.MessageKind)."""

from decimal import Decimal

import pytest

from chat.models import ChatMessage, ChatSession
from chat.V2.agent import AgentResponse
from chat.V2.logging.turn_logging_service import TurnLoggingService


@pytest.mark.django_db
class TestPersistNarrationSegments:
    @pytest.fixture
    def session(self):
        return ChatSession.objects.create(session_id="sess_narration_test", user_id="user_1")

    @pytest.fixture
    def user_message(self, session):
        return ChatMessage.objects.create(
            message_id=ChatMessage.generate_message_id(),
            session_id=session.session_id,
            user_id="user_1",
            turn_id="turn_1",
            role=ChatMessage.Role.USER,
            content="What's the Daf Yomi today?",
        )

    def _turn_messages(self, user_message):
        return list(
            ChatMessage.objects.filter(turn_id=user_message.turn_id).order_by("server_timestamp")
        )

    def test_each_narration_segment_gets_its_own_row_in_order(self, session, user_message):
        resp = AgentResponse(
            content="Today's Daf Yomi is Bekhorot 33, with linked sources.",
            tool_calls=[{"name": "get_current_calendar"}, {"name": "semantic_search"}],
            latency_ms=500,
            narration_segments=[
                "Let me check the calendar.",
                "Today's Daf Yomi is Bekhorot 33. It concerns blemishes.",
            ],
        )
        svc = TurnLoggingService()
        result = svc.persist_assistant_response(
            user_message=user_message,
            agent_response=resp,
            latency_ms=500,
            model_name="claude-sonnet-4-5",
        )

        rows = self._turn_messages(user_message)
        # The user's own row, plus one per narration segment, plus the final answer.
        assert [m.content for m in rows] == [
            "What's the Daf Yomi today?",
            "Let me check the calendar.",
            "Today's Daf Yomi is Bekhorot 33. It concerns blemishes.",
            "Today's Daf Yomi is Bekhorot 33, with linked sources.",
        ]
        assert [m.message_kind for m in rows[1:]] == [
            ChatMessage.MessageKind.NARRATION,
            ChatMessage.MessageKind.NARRATION,
            ChatMessage.MessageKind.FINAL_RESPONSE,
        ]
        # response_message still points only at the final row, not a narration one.
        assert result.response_message.message_kind == ChatMessage.MessageKind.FINAL_RESPONSE
        assert result.response_message.content == resp.content
        # The caller (the live turn) gets the same narration rows back, in order,
        # so it can show them on screen instead of just the final answer.
        assert [m.content for m in result.narration_messages] == resp.narration_segments

    def test_narration_rows_carry_no_aggregate_metrics(self, session, user_message):
        resp = AgentResponse(
            content="Final answer.",
            tool_calls=[{"name": "semantic_search"}],
            latency_ms=500,
            narration_segments=["Let me search for that."],
            input_tokens=1000,
            output_tokens=300,
            total_cost_usd=0.05,
        )
        svc = TurnLoggingService()
        svc.persist_assistant_response(
            user_message=user_message,
            agent_response=resp,
            latency_ms=500,
            model_name="claude-sonnet-4-5",
        )

        narration_row = ChatMessage.objects.get(
            turn_id=user_message.turn_id, message_kind=ChatMessage.MessageKind.NARRATION
        )
        assert narration_row.input_tokens is None
        assert narration_row.output_tokens is None
        assert narration_row.total_cost_usd is None
        assert narration_row.tool_calls_data is None

        final_row = ChatMessage.objects.get(
            turn_id=user_message.turn_id, message_kind=ChatMessage.MessageKind.FINAL_RESPONSE
        )
        assert final_row.input_tokens == 1000
        assert final_row.total_cost_usd == Decimal("0.05")

    def test_no_narration_rows_when_no_segments(self, session, user_message):
        resp = AgentResponse(content="Final answer.", tool_calls=[], latency_ms=100)
        svc = TurnLoggingService()
        svc.persist_assistant_response(
            user_message=user_message,
            agent_response=resp,
            latency_ms=100,
            model_name="claude-sonnet-4-5",
        )

        assert not ChatMessage.objects.filter(
            turn_id=user_message.turn_id, message_kind=ChatMessage.MessageKind.NARRATION
        ).exists()
