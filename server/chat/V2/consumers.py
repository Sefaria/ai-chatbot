"""Websocket consumer for v2 chat."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.utils import timezone

from .agent import AgentProgressUpdate, ClaudeAgentService, ConversationMessage
from .logging import get_turn_logging_service
from .summarization import get_summary_service
from ..models import ChatMessage, ChatSession, ConversationSummary
from ..serializers import ChatRequestSerializer
from ..user_token_service import (
    UserTokenError,
    UserTokenExpiredError,
    decrypt_chatbot_user_token,
)

_agent_service: ClaudeAgentService | None = None


def _apply_page_context_to_user_message(message: str, page_url: str) -> str:
    if not page_url:
        return message
    return (
        f"{message}\n\n"
        f"User is currently on the Sefaria url: {page_url}. "
        "If the context is relevant, use that information in your response"
    )


def get_agent_service() -> ClaudeAgentService:
    global _agent_service
    if _agent_service is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is required")
        _agent_service = ClaudeAgentService(api_key=api_key)
    return _agent_service


class V2ChatConsumer(AsyncJsonWebsocketConsumer):
    """Websocket consumer for v2 chat."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        await self.accept()

    async def receive_json(self, content: dict[str, Any], **kwargs: Any) -> None:
        async with self._lock:
            await self._handle_request(content)

    async def _handle_request(self, content: dict[str, Any]) -> None:
        start_time = time.time()
        serializer = ChatRequestSerializer(data=content)
        if not serializer.is_valid():
            await self.send_json(
                {
                    "event": "error",
                    "error": "invalid_request",
                    "details": serializer.errors,
                }
            )
            return

        data = serializer.validated_data
        secret = settings.CHATBOT_USER_TOKEN_SECRET
        if not secret:
            await self.send_json({"event": "error", "error": "userId_decryption_unavailable"})
            return

        try:
            decrypted_user_id = await sync_to_async(decrypt_chatbot_user_token)(
                data["userId"], secret
            )
        except UserTokenExpiredError:
            await self.send_json({"event": "error", "error": "userId_expired"})
            return
        except UserTokenError:
            await self.send_json({"event": "error", "error": "invalid_userId"})
            return

        data["userId"] = decrypted_user_id
        context = data.get("context", {})
        page_url = context.get("pageUrl", "")
        prompt_slugs = data.get("promptSlugs") or {}
        turn_id = ChatMessage.generate_turn_id()

        session = await database_sync_to_async(self._get_or_create_session)(data)

        summary_text = await database_sync_to_async(self._load_summary_text)(session)

        core_prompt_slug = (prompt_slugs.get("corePromptSlug") or "").strip()
        if not core_prompt_slug:
            core_prompt_slug = settings.CORE_PROMPT_SLUG

        user_message = await database_sync_to_async(self._create_user_message)(
            data=data,
            turn_id=turn_id,
        )

        user_content = _apply_page_context_to_user_message(data["text"], page_url)
        conversation = [ConversationMessage(role="user", content=user_content)]

        def on_progress(update: AgentProgressUpdate) -> None:
            payload: dict[str, Any] = {
                "event": "progress",
                "type": update.type,
                "text": update.text,
            }
            if update.tool_name:
                payload["toolName"] = update.tool_name
            if update.tool_input:
                payload["toolInput"] = update.tool_input
            if update.description:
                payload["description"] = update.description
            if update.is_error is not None:
                payload["isError"] = update.is_error
            if update.output_preview:
                payload["outputPreview"] = update.output_preview
            asyncio.create_task(self.send_json(payload))

        try:
            agent = get_agent_service()
            agent_response = await agent.send_message(
                messages=conversation,
                core_prompt_id=core_prompt_slug,
                on_progress=on_progress,
                summary_text=summary_text,
            )
        except Exception as exc:
            logging_service = get_turn_logging_service()
            error_message = await database_sync_to_async(logging_service.record_error_message)(
                session_id=data["sessionId"],
                user_id=data["userId"],
                turn_id=turn_id,
                latency_ms=int((time.time() - start_time) * 1000),
                error_text="I'm sorry, I encountered an error processing your request.",
            )
            await database_sync_to_async(self._attach_error_response)(user_message, error_message)
            await self.send_json({"event": "error", "error": str(exc)})
            return

        summary_service = get_summary_service()
        new_summary = await database_sync_to_async(summary_service.update_summary)(
            session=session,
            new_user_message=data["text"],
            new_assistant_response=agent_response.content,
        )

        logging_service = get_turn_logging_service()
        logging_result = await database_sync_to_async(logging_service.finalize_success)(
            session=session,
            user_message=user_message,
            agent_response=agent_response,
            latency_ms=int((time.time() - start_time) * 1000),
            model_name="claude-sonnet-4-5-20250929",
            summary_text=new_summary.to_prompt_text(),
        )

        session = await database_sync_to_async(self._refresh_session)(session)

        await self.send_json(
            {
                "event": "message",
                "messageId": logging_result.response_message.message_id,
                "sessionId": data["sessionId"],
                "timestamp": logging_result.response_message.server_timestamp.isoformat(),
                "markdown": agent_response.content,
                "traceId": agent_response.trace_id,
                "toolCalls": agent_response.tool_calls,
                "session": {"turnCount": session.turn_count or 0},
                "stats": logging_result.stats,
            }
        )

    @staticmethod
    def _get_or_create_session(data: dict[str, Any]) -> ChatSession:
        session, _ = ChatSession.objects.update_or_create(
            session_id=data["sessionId"],
            defaults={
                "user_id": data["userId"],
                "last_activity": timezone.now(),
            },
        )
        return session

    @staticmethod
    def _load_summary_text(session: ChatSession) -> str:
        summary = ConversationSummary.objects.filter(session=session).first()
        return summary.to_prompt_text() if summary else ""

    @staticmethod
    def _create_user_message(data: dict[str, Any], turn_id: str) -> ChatMessage:
        context = data.get("context", {})
        return ChatMessage.objects.create(
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

    @staticmethod
    def _attach_error_response(user_message: ChatMessage, error_message: ChatMessage) -> None:
        user_message.response_message = error_message
        user_message.save(update_fields=["response_message"])

    @staticmethod
    def _refresh_session(session: ChatSession) -> ChatSession:
        session.refresh_from_db()
        return session
