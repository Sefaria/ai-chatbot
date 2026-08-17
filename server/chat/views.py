"""Shared chat API views."""

import logging
from datetime import datetime
from urllib.parse import urlparse

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .auth import AuthenticationError, authenticate_request
from .models import ChatMessage, ChatSession
from .serializers import (
    HistoryMessageSerializer,
    RenameConversationSerializer,
    SavedConversationSerializer,
)
from .V2 import views as v2_views
from .V2.prompts import get_prompt_service

logger = logging.getLogger("chat")


def _resolve_user_id(request, raw_user_id: str) -> str:
    """
    Resolve encrypted chatbot user tokens to the DB user id used by chat persistence.

    Local tests and some dev tools still pass plain ids, so fall back to the raw value
    when token auth cannot decode it.
    """
    try:
        return authenticate_request(request, {"userId": raw_user_id}).user_id
    except AuthenticationError:
        return raw_user_id


def extract_page_type(url: str | None) -> str:
    """Classify Sefaria page types for telemetry."""
    if not url:
        return "unknown"

    try:
        parsed = urlparse(url)
    except Exception:
        return "reader"

    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()

    if not host:
        return "reader"
    if host.startswith("eval."):
        return "eval"
    if host.startswith("staging."):
        return "staging"
    if "sefaria.org" in host:
        if path in ("/texts", "/texts/"):
            return "home"
        if path.startswith("/static/"):
            return "other"
        if path in ("", "/"):
            return "other"
        return "reader"
    return "reader"


@api_view(["GET"])
def history(request):
    """
    Get conversation history with session metadata.

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
    user_id = _resolve_user_id(request, user_id)

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
            "turnCount": session.turn_count,
            "totalTokens": (session.total_input_tokens or 0) + (session.total_output_tokens or 0),
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


def _saved_conversation_queryset(user_id: str):
    return ChatSession.objects.filter(
        user_id=user_id,
        is_deleted=False,
        turn_count__gt=0,
    )


@api_view(["GET"])
def conversations(request):
    """
    List saved conversations for a user.

    GET /api/conversations?userId=...&q=...&limit=...
    """
    user_id = request.query_params.get("userId")
    query = (request.query_params.get("q") or "").strip()

    try:
        limit = min(max(int(request.query_params.get("limit", 50)), 1), 100)
    except ValueError:
        return Response({"error": "Invalid limit"}, status=status.HTTP_400_BAD_REQUEST)

    if not user_id:
        return Response({"error": "userId is required"}, status=status.HTTP_400_BAD_REQUEST)
    user_id = _resolve_user_id(request, user_id)

    queryset = _saved_conversation_queryset(user_id)

    if query:
        matching_session_ids = ChatMessage.objects.filter(
            user_id=user_id,
            content__icontains=query,
        ).values("session_id")
        queryset = queryset.filter(
            Q(title__icontains=query) | Q(session_id__in=matching_session_ids)
        )

    sessions = queryset.order_by("-last_activity")[:limit]
    return Response({"conversations": SavedConversationSerializer(sessions, many=True).data})


@api_view(["GET", "PATCH", "DELETE"])
def conversation_detail(request, session_id):
    """
    Load, rename, or permanently delete a saved conversation.

    GET /api/conversations/<sessionId>?userId=...
    PATCH /api/conversations/<sessionId> { userId, title }
    DELETE /api/conversations/<sessionId>?userId=...
    """
    user_id = request.query_params.get("userId") or request.data.get("userId")
    if not user_id:
        return Response({"error": "userId is required"}, status=status.HTTP_400_BAD_REQUEST)
    user_id = _resolve_user_id(request, user_id)

    session = ChatSession.objects.filter(
        session_id=session_id,
        user_id=user_id,
        is_deleted=False,
    ).first()
    if not session:
        return Response({"error": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        messages = ChatMessage.objects.filter(
            user_id=user_id,
            session_id=session_id,
        ).order_by("server_timestamp")
        return Response(
            {
                "conversation": SavedConversationSerializer(session).data,
                "messages": HistoryMessageSerializer(messages, many=True).data,
            }
        )

    if request.method == "PATCH":
        serializer = RenameConversationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session.title = serializer.validated_data["title"][:64]
        session.title_updated_at = timezone.now()
        session.save(update_fields=["title", "title_updated_at", "last_activity"])
        return Response({"conversation": SavedConversationSerializer(session).data})

    with transaction.atomic():
        ChatMessage.objects.filter(user_id=user_id, session_id=session_id).delete()
        if hasattr(session, "summary"):
            session.summary.delete()
        session.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["POST"])
def reload_prompts(request):
    """
    Reload prompts from Braintrust without restarting the server.

    POST /api/admin/reload-prompts
    """
    try:
        prompt_service = get_prompt_service()
        prompt_service.invalidate_cache()

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
    agent_ok = False
    try:
        agent_ok = v2_views.get_agent_service() is not None
    except Exception:
        agent_ok = False

    return Response(
        {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "services": {
                "agent": agent_ok,
                "braintrust": True,  # Native tracing always available
            },
            "versions": ["v2"],
        }
    )
