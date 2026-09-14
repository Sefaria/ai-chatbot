"""Shared chat API views."""

import logging
import operator
import re
from datetime import datetime
from functools import reduce
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchVector
from django.db import connection
from django.db.models import Q, TextField
from django.db.models.functions import Cast
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import ChatMessage, ChatSession
from .serializers import HistoryMessageSerializer
from .user_token_service import UserTokenError, decrypt_chatbot_user_identity
from .V2 import views as v2_views
from .V2.prompts import get_prompt_service

logger = logging.getLogger("chat")

DEFAULT_HISTORY_LIMIT = 20
MAX_HISTORY_LIMIT = 100
MAX_TITLE_LENGTH = 64


def _resolve_history_user_ids(user_id: str) -> list[str]:
    """
    Return possible persisted chatbot DB user ids for history reads.

    Current clients pass the encrypted Sefaria chatbot token. Older callers and
    tests may still pass the already-persisted id directly, so invalid token
    input falls back to the original value. During the raw-id anonymization
    rollout, a user may temporarily have rows under both the anonymized id and
    the raw Sefaria id; reads accept both while writes persist only the
    anonymized id.
    """
    secret = settings.CHATBOT_USER_TOKEN_SECRET
    if not secret:
        return [user_id]

    try:
        identity = decrypt_chatbot_user_identity(user_id, secret)
    except UserTokenError:
        return [user_id]

    user_ids = [identity.user_id]
    if identity.sefaria_user_id and identity.sefaria_user_id not in user_ids:
        user_ids.append(identity.sefaria_user_id)
    return user_ids


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


def _read_limit_offset(request) -> tuple[int, int]:
    try:
        limit = int(request.query_params.get("limit", DEFAULT_HISTORY_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_HISTORY_LIMIT
    try:
        offset = int(request.query_params.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    return max(1, min(limit, MAX_HISTORY_LIMIT)), max(0, offset)


def _default_conversation_title(content: str) -> str:
    return " ".join((content or "").split())[:MAX_TITLE_LENGTH]


def _ensure_session_titles(sessions: list[ChatSession]) -> None:
    """
    Backfill missing conversation titles using the MVP title rule:
    first 64 chars of the first user prompt.

    This keeps older sessions usable without a migration over all historical rows.
    """
    missing_title_sessions = [session for session in sessions if not session.title]
    if not missing_title_sessions:
        return

    session_by_id = {session.session_id: session for session in missing_title_sessions}
    first_prompt_by_session: dict[str, str] = {}
    messages = (
        ChatMessage.objects.filter(
            session_id__in=session_by_id.keys(),
            role=ChatMessage.Role.USER,
        )
        .order_by("session_id", "server_timestamp", "id")
        .values("session_id", "content")
    )
    for message in messages:
        first_prompt_by_session.setdefault(message["session_id"], message["content"])

    now = timezone.now()
    sessions_to_update = []
    for session_id, content in first_prompt_by_session.items():
        title = _default_conversation_title(content)
        if not title:
            continue
        session = session_by_id[session_id]
        session.title = title
        session.title_updated_at = now
        sessions_to_update.append(session)

    if sessions_to_update:
        ChatSession.objects.bulk_update(sessions_to_update, ["title", "title_updated_at"])


def _session_summary(session: ChatSession) -> dict:
    return {
        "sessionId": session.session_id,
        "title": session.title or "",
        "createdAt": session.created_at.isoformat(),
        "lastActivity": session.last_activity.isoformat(),
        "messageCount": session.message_count,
        "turnCount": session.turn_count,
    }


def _get_owned_session(session_id: str, user_id_candidates: list[str]) -> ChatSession | None:
    return ChatSession.objects.filter(
        session_id=session_id,
        user_id__in=user_id_candidates,
        is_deleted=False,
    ).first()


def _build_search_query(query_text: str):
    tokens = re.findall(r"[\w]+", query_text, flags=re.UNICODE)
    if not tokens:
        return None
    queries = [
        SearchQuery(token, config="simple", search_type="plain")
        for token in tokens
        if token.strip()
    ]
    return reduce(operator.or_, queries) if queries else None


def _search_session_ids(user_id_candidates: list[str], query_text: str) -> set[str]:
    if connection.vendor == "postgresql":
        query = _build_search_query(query_text)
        if query is None:
            return set()
        title_matches = (
            ChatSession.objects.filter(user_id__in=user_id_candidates, is_deleted=False)
            .annotate(search=SearchVector("title", config="simple"))
            .filter(search=query)
            .values_list("session_id", flat=True)
        )
        message_matches = (
            ChatMessage.objects.filter(user_id__in=user_id_candidates)
            .annotate(
                search=SearchVector("content", config="simple")
                + SearchVector(Cast("appetizer_data", TextField()), config="simple")
            )
            .filter(search=query)
            .values_list("session_id", flat=True)
        )
        return set(title_matches) | set(message_matches)

    # Test/dev fallback for non-Postgres databases.
    return set(
        ChatSession.objects.filter(
            Q(title__icontains=query_text)
            | Q(
                session_id__in=ChatMessage.objects.filter(
                    Q(content__icontains=query_text) | Q(appetizer_data__icontains=query_text)
                ).values("session_id")
            ),
            user_id__in=user_id_candidates,
            is_deleted=False,
        ).values_list("session_id", flat=True)
    )


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

    user_id_candidates = _resolve_history_user_ids(user_id)

    queryset = ChatMessage.objects.filter(
        user_id__in=user_id_candidates,
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
        session = ChatSession.objects.get(
            session_id=session_id,
            user_id__in=user_id_candidates,
            is_deleted=False,
        )
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


@api_view(["GET"])
def conversation_list(request):
    """
    List saved conversations.

    GET /api/history/conversations?userId=...&limit=20&offset=0&search=...
    """
    user_id = request.query_params.get("userId")
    if not user_id:
        return Response({"error": "userId is required"}, status=status.HTTP_400_BAD_REQUEST)

    limit, offset = _read_limit_offset(request)
    user_id_candidates = _resolve_history_user_ids(user_id)
    search_text = (request.query_params.get("search") or "").strip()

    queryset = ChatSession.objects.filter(
        user_id__in=user_id_candidates,
        is_deleted=False,
    ).filter(Q(message_count__gt=0) | Q(turn_count__gt=0))

    if search_text:
        matching_session_ids = _search_session_ids(user_id_candidates, search_text)
        queryset = queryset.filter(session_id__in=matching_session_ids)

    total = queryset.count()
    sessions = list(queryset.order_by("-last_activity")[offset : offset + limit])
    _ensure_session_titles(sessions)

    return Response(
        {
            "conversations": [_session_summary(session) for session in sessions],
            "hasMore": offset + len(sessions) < total,
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    )


@api_view(["GET", "PATCH", "DELETE"])
def conversation_detail(request, session_id: str):
    user_id = request.query_params.get("userId") or request.data.get("userId")
    if not user_id:
        return Response({"error": "userId is required"}, status=status.HTTP_400_BAD_REQUEST)

    user_id_candidates = _resolve_history_user_ids(user_id)
    session = _get_owned_session(session_id, user_id_candidates)
    if session is None:
        return Response({"error": "conversation_not_found"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "PATCH":
        title = " ".join((request.data.get("title") or "").split())[:MAX_TITLE_LENGTH]
        if not title:
            return Response({"error": "title_required"}, status=status.HTTP_400_BAD_REQUEST)
        session.title = title
        session.title_updated_at = timezone.now()
        session.save(update_fields=["title", "title_updated_at"])
        return Response({"conversation": _session_summary(session)})

    if request.method == "DELETE":
        now = timezone.now()
        session.is_deleted = True
        session.deleted_at = now
        session.save(update_fields=["is_deleted", "deleted_at"])
        return Response({"success": True})

    _ensure_session_titles([session])
    messages = ChatMessage.objects.filter(
        user_id__in=user_id_candidates,
        session_id=session.session_id,
    ).order_by("server_timestamp")
    serializer = HistoryMessageSerializer(messages, many=True)
    return Response(
        {
            "conversation": _session_summary(session),
            "messages": serializer.data,
            "session": {
                "turnCount": session.turn_count,
                "totalTokens": (session.total_input_tokens or 0)
                + (session.total_output_tokens or 0),
            },
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
