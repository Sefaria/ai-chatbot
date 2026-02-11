"""Turn logging for V2 chat requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.utils import timezone

from ...auth import Actor
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
        actor: Actor,
        turn_id: str,
        latency_ms: int,
        error_text: str,
    ) -> ChatMessage:
        return ChatMessage.objects.create(
            message_id=ChatMessage.generate_message_id(),
            session_id=session_id,
            turn_id=turn_id,
            role=ChatMessage.Role.ASSISTANT,
            content=error_text,
            status=ChatMessage.Status.FAILED,
            latency_ms=latency_ms,
            **actor.to_db_fields(),
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
            status=ChatMessage.Status.SUCCESS,
            input_tokens=agent_response.input_tokens,
            output_tokens=agent_response.output_tokens,
            cache_creation_tokens=agent_response.cache_creation_tokens,
            cache_read_tokens=agent_response.cache_read_tokens,
            total_cost_usd=agent_response.total_cost_usd,
        )

        user_message.response_message = response_message
        user_message.latency_ms = latency_ms
        user_message.save(update_fields=["response_message", "latency_ms"])

        session.message_count = ChatMessage.objects.filter(
            session_id=user_message.session_id
        ).count()
        session.turn_count = (session.turn_count or 0) + 1
        session.conversation_summary = summary_text
        session.summary_updated_at = timezone.now()
        session.total_tool_calls = (session.total_tool_calls or 0) + len(agent_response.tool_calls)
        if agent_response.input_tokens is not None:
            total_prompt = (
                agent_response.input_tokens
                + (agent_response.cache_read_tokens or 0)
                + (agent_response.cache_creation_tokens or 0)
            )
            session.total_input_tokens = (session.total_input_tokens or 0) + total_prompt
        if agent_response.output_tokens:
            session.total_output_tokens = (
                session.total_output_tokens or 0
            ) + agent_response.output_tokens
        if agent_response.total_cost_usd is not None:
            from decimal import Decimal

            session.total_cost_usd = (session.total_cost_usd or Decimal(0)) + Decimal(
                str(agent_response.total_cost_usd)
            )
        session.save(
            update_fields=[
                "message_count",
                "turn_count",
                "last_activity",
                "conversation_summary",
                "summary_updated_at",
                "total_tool_calls",
                "total_input_tokens",
                "total_output_tokens",
                "total_cost_usd",
            ]
        )

        stats = self.build_stats(agent_response=agent_response, latency_ms=latency_ms)
        return TurnLoggingResult(response_message=response_message, stats=stats)

    @staticmethod
    def build_stats(*, agent_response: AgentResponse, latency_ms: int) -> dict[str, Any]:
        stats: dict[str, Any] = {
            "llmCalls": agent_response.llm_calls,
            "toolCalls": len(agent_response.tool_calls),
            "latencyMs": latency_ms,
        }
        if agent_response.input_tokens is not None:
            # Total prompt tokens = base + cache_read + cache_creation.
            # Anthropic's raw input_tokens excludes cached tokens.
            stats["inputTokens"] = (
                agent_response.input_tokens
                + (agent_response.cache_read_tokens or 0)
                + (agent_response.cache_creation_tokens or 0)
            )
        if agent_response.output_tokens is not None:
            stats["outputTokens"] = agent_response.output_tokens
        if agent_response.cache_read_tokens is not None:
            stats["cacheReadTokens"] = agent_response.cache_read_tokens
        if agent_response.cache_creation_tokens is not None:
            stats["cacheCreationTokens"] = agent_response.cache_creation_tokens
        if agent_response.total_cost_usd is not None:
            stats["totalCostUsd"] = agent_response.total_cost_usd
        return stats


_logging_service: TurnLoggingService | None = None


def get_turn_logging_service() -> TurnLoggingService:
    """Get a singleton logging service instance."""
    global _logging_service
    if _logging_service is None:
        _logging_service = TurnLoggingService()
    return _logging_service
