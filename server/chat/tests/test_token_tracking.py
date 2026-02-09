"""Tests for Phase 3 token tracking — ResultMessage → AgentResponse → span/DB/API."""

import pytest

from chat.models import ChatMessage, ChatSession
from chat.V2.agent import AgentResponse
from chat.V2.logging.turn_logging_service import TurnLoggingService

# -- AgentResponse dataclass --------------------------------------------------


class TestAgentResponseTokenFields:
    def test_defaults_to_none(self):
        resp = AgentResponse(content="hi", tool_calls=[], latency_ms=100)
        assert resp.input_tokens is None
        assert resp.output_tokens is None
        assert resp.cache_creation_tokens is None
        assert resp.cache_read_tokens is None
        assert resp.total_cost_usd is None

    def test_accepts_token_values(self):
        resp = AgentResponse(
            content="hi",
            tool_calls=[],
            latency_ms=100,
            input_tokens=500,
            output_tokens=200,
            cache_creation_tokens=1000,
            cache_read_tokens=3000,
            total_cost_usd=0.05,
        )
        assert resp.input_tokens == 500
        assert resp.output_tokens == 200
        assert resp.cache_creation_tokens == 1000
        assert resp.cache_read_tokens == 3000
        assert resp.total_cost_usd == 0.05


# -- build_stats ---------------------------------------------------------------


class TestBuildStatsTokens:
    def test_includes_tokens_when_present(self):
        resp = AgentResponse(
            content="hi",
            tool_calls=[],
            latency_ms=100,
            input_tokens=500,
            output_tokens=200,
            total_cost_usd=0.05,
        )
        stats = TurnLoggingService.build_stats(agent_response=resp, latency_ms=100)
        assert stats["inputTokens"] == 500
        assert stats["outputTokens"] == 200
        assert stats["totalCostUsd"] == 0.05

    def test_omits_tokens_when_none(self):
        resp = AgentResponse(content="hi", tool_calls=[], latency_ms=100)
        stats = TurnLoggingService.build_stats(agent_response=resp, latency_ms=100)
        assert "inputTokens" not in stats
        assert "outputTokens" not in stats
        assert "totalCostUsd" not in stats


# -- DB persistence ------------------------------------------------------------


@pytest.mark.django_db
class TestFinalizeSuccessTokens:
    @pytest.fixture
    def session(self):
        return ChatSession.objects.create(
            session_id="sess_tok_test",
            user_id="user_1",
        )

    @pytest.fixture
    def user_message(self, session):
        return ChatMessage.objects.create(
            message_id=ChatMessage.generate_message_id(),
            session_id=session.session_id,
            user_id="user_1",
            turn_id="turn_1",
            role=ChatMessage.Role.USER,
            content="What is Shabbat?",
        )

    def test_tokens_persisted_to_message(self, session, user_message):
        resp = AgentResponse(
            content="Shabbat is the day of rest.",
            tool_calls=[],
            latency_ms=500,
            input_tokens=1000,
            output_tokens=300,
            cache_creation_tokens=2000,
            cache_read_tokens=5000,
            total_cost_usd=0.05,
        )
        svc = TurnLoggingService()
        result = svc.finalize_success(
            session=session,
            user_message=user_message,
            agent_response=resp,
            latency_ms=500,
            model_name="claude-sonnet-4-5",
            summary_text="",
        )
        msg = result.response_message
        assert msg.input_tokens == 1000
        assert msg.output_tokens == 300
        assert msg.cache_creation_tokens == 2000
        assert msg.cache_read_tokens == 5000
        assert msg.total_cost_usd == 0.05

    def test_session_aggregates_updated(self, session, user_message):
        resp = AgentResponse(
            content="Answer",
            tool_calls=[],
            latency_ms=100,
            input_tokens=800,
            output_tokens=200,
            total_cost_usd=0.03,
        )
        svc = TurnLoggingService()
        svc.finalize_success(
            session=session,
            user_message=user_message,
            agent_response=resp,
            latency_ms=100,
            model_name="claude-sonnet-4-5",
            summary_text="",
        )
        session.refresh_from_db()
        assert session.total_input_tokens == 800
        assert session.total_output_tokens == 200

    def test_none_tokens_leave_session_unchanged(self, session, user_message):
        resp = AgentResponse(
            content="Answer",
            tool_calls=[],
            latency_ms=100,
        )
        svc = TurnLoggingService()
        svc.finalize_success(
            session=session,
            user_message=user_message,
            agent_response=resp,
            latency_ms=100,
            model_name="claude-sonnet-4-5",
            summary_text="",
        )
        session.refresh_from_db()
        assert session.total_input_tokens == 0
        assert session.total_output_tokens == 0

    def test_session_aggregates_accumulate(self, session):
        """Two turns should accumulate token counts and cost."""
        svc = TurnLoggingService()
        for i in range(2):
            user_msg = ChatMessage.objects.create(
                message_id=ChatMessage.generate_message_id(),
                session_id=session.session_id,
                user_id="user_1",
                turn_id=f"turn_{i}",
                role=ChatMessage.Role.USER,
                content=f"Question {i}",
            )
            resp = AgentResponse(
                content=f"Answer {i}",
                tool_calls=[],
                latency_ms=100,
                input_tokens=500,
                output_tokens=100,
                total_cost_usd=0.02,
            )
            svc.finalize_success(
                session=session,
                user_message=user_msg,
                agent_response=resp,
                latency_ms=100,
                model_name="claude-sonnet-4-5",
                summary_text="",
            )
        session.refresh_from_db()
        assert session.total_input_tokens == 1000
        assert session.total_output_tokens == 200
