"""Shared chat API views."""

import logging
from datetime import datetime
from urllib.parse import urlparse

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .auth import (
    AuthenticationRequired,
    InvalidAPIKey,
    InvalidUserToken,
    UserTokenExpired,
    authenticate_request,
)
from .models import ChatMessage, ChatSession
from .serializers import HistoryMessageSerializer
from .V2 import views as v2_views
from .V2.prompts import get_prompt_service

logger = logging.getLogger("chat")


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

    Supports dual authentication:
    - API key: Authorization: Bearer <key> (filter by service_id)
    - User token: userId query param (filter by user_id)

    GET /api/history?sessionId=...&before=...&limit=...
    GET /api/history?userId=...&sessionId=...&before=...&limit=...  (legacy)
    """
    session_id = request.query_params.get("sessionId")
    before = request.query_params.get("before")
    limit = min(int(request.query_params.get("limit", 20)), 100)

    if not session_id:
        return Response({"error": "sessionId is required"}, status=status.HTTP_400_BAD_REQUEST)

    # Authenticate - try API key first, then user token from query param
    try:
        # Build body_data from query params for user token auth
        body_data = {}
        if request.query_params.get("userId"):
            body_data["userId"] = request.query_params.get("userId")

        actor = authenticate_request(request, body_data)
    except UserTokenExpired:
        return Response({"error": "userId_expired"}, status=status.HTTP_401_UNAUTHORIZED)
    except (InvalidUserToken, AuthenticationRequired):
        return Response({"error": "invalid_userId"}, status=status.HTTP_401_UNAUTHORIZED)
    except InvalidAPIKey:
        return Response({"error": "invalid_api_key"}, status=status.HTTP_401_UNAUTHORIZED)

    # Build query based on actor type
    if actor.is_service:
        queryset = ChatMessage.objects.filter(
            service_id=actor.service_id,
            session_id=session_id,
        )
    else:
        queryset = ChatMessage.objects.filter(
            user_id=actor.user_id,
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

    # Get session info - verify ownership
    try:
        session = ChatSession.objects.get(session_id=session_id)
        # Verify session belongs to this actor
        if actor.is_service:
            if session.service_id != actor.service_id:
                session_info = None
            else:
                session_info = {
                    "turnCount": session.turn_count or 0,
                    "totalTokens": (session.total_input_tokens or 0)
                    + (session.total_output_tokens or 0),
                }
        else:
            if session.user_id != actor.user_id:
                session_info = None
            else:
                session_info = {
                    "turnCount": session.turn_count or 0,
                    "totalTokens": (session.total_input_tokens or 0)
                    + (session.total_output_tokens or 0),
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
