"""Turn logging for V2 chat requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.utils import timezone

from ...models import ChatMessage, ChatSession
from ..agent import AgentResponse


@dataclass
class TurnLoggingResult:
    """Result from persisting an assistant response."""

    response_message: ChatMessage
    stats: dict[str, Any]


class TurnLoggingService:
    """Encapsulates DB logging and session updates for a chat turn."""

    def record_error_message(
        self,
        *,
        session_id: str,
        user_id: str,
        turn_id: str,
        latency_ms: int,
        error_text: str,
    ) -> ChatMessage:
        return ChatMessage.objects.create(
            message_id=ChatMessage.generate_message_id(),
            session_id=session_id,
            user_id=user_id,
            turn_id=turn_id,
            role=ChatMessage.Role.ASSISTANT,
            content=error_text,
            status=ChatMessage.Status.FAILED,
            latency_ms=latency_ms,
            flow="",
        )

    def finalize_success(
        self,
        *,
        session: ChatSession,
        user_message: ChatMessage,
        agent_response: AgentResponse,
        latency_ms: int,
        model_name: str,
        summary_text: str,
    ) -> TurnLoggingResult:
        response_message = ChatMessage.objects.create(
            message_id=ChatMessage.generate_message_id(),
            session_id=user_message.session_id,
            user_id=user_message.user_id,
            turn_id=user_message.turn_id,
            role=ChatMessage.Role.ASSISTANT,
            content=agent_response.content,
            latency_ms=latency_ms,
            llm_calls=agent_response.llm_calls,
            tool_calls_count=len(agent_response.tool_calls),
            tool_calls_data=agent_response.tool_calls,
            model_name=model_name,
            flow="",
            status=ChatMessage.Status.SUCCESS,
        )

        user_message.response_message = response_message
        user_message.latency_ms = latency_ms
        user_message.save(update_fields=["response_message", "latency_ms"])

        session.message_count = ChatMessage.objects.filter(session_id=user_message.session_id).count()
        session.turn_count = (session.turn_count or 0) + 1
        session.current_flow = ""
        session.conversation_summary = summary_text
        session.summary_updated_at = timezone.now()
        session.total_tool_calls = (session.total_tool_calls or 0) + len(agent_response.tool_calls)
        session.save(
            update_fields=[
                "message_count",
                "turn_count",
                "last_activity",
                "current_flow",
                "conversation_summary",
                "summary_updated_at",
                "total_tool_calls",
            ]
        )

        stats = self.build_stats(agent_response=agent_response, latency_ms=latency_ms)
        return TurnLoggingResult(response_message=response_message, stats=stats)

    @staticmethod
    def build_stats(*, agent_response: AgentResponse, latency_ms: int) -> dict[str, Any]:
        return {
            "llmCalls": agent_response.llm_calls,
            "toolCalls": len(agent_response.tool_calls),
            "latencyMs": latency_ms,
        }


_logging_service: TurnLoggingService | None = None


def get_turn_logging_service() -> TurnLoggingService:
    """Get a singleton logging service instance."""
    global _logging_service
    if _logging_service is None:
        _logging_service = TurnLoggingService()
    return _logging_service
