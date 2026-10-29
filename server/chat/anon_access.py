"""
The free-answer limit for logged-out (anonymous) visitors.

An admin sets the number in Sefaria's Django admin, as the remote config entry
`feature.chatbot.anonymous_free_answers`. This server reads it from the request's
Sefaria's public /api/remote-config (see sefaria_base_url.py). There is no default here: if the key isn't in
Sefaria's config (for example when this server runs without Sefaria), there is
no limit.
"""

import logging

import httpx
from django.core.cache import cache

from .models import ChatMessage
from .V2.agent.sefaria_client import _get_default_sefaria_base_url

logger = logging.getLogger("chat")

REMOTE_CONFIG_KEY = "feature.chatbot.anonymous_free_answers"
CACHE_KEY = "anon_access:free_answer_limit"
CACHE_SECONDS = 60
REQUEST_TIMEOUT_SECONDS = 3


def get_anon_answer_limit() -> int | None:
    """How many answers a logged-out visitor gets, or None for no limit.

    Read from the current request's Sefaria, cached per Sefaria.
    """
    base_url = _get_default_sefaria_base_url().rstrip("/")
    cache_key = f"{CACHE_KEY}:{base_url}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached["limit"]

    limit = _fetch_limit(base_url)
    # Cache "no limit" too, so an unreachable Sefaria isn't asked on every question.
    cache.set(cache_key, {"limit": limit}, CACHE_SECONDS)
    return limit


def _fetch_limit(base_url: str) -> int | None:
    url = f"{base_url}/api/remote-config"
    try:
        response = httpx.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        config = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Could not read remote config from Sefaria: %s", exc)
        return None

    value = config.get(REMOTE_CONFIG_KEY) if isinstance(config, dict) else None
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        logger.warning("Ignoring invalid %s in remote config: %r", REMOTE_CONFIG_KEY, value)
        return None
    return value


def count_anon_answers(user_id: str) -> int:
    """Answers a logged-out visitor got, across all their sessions.

    Only real answers count: refused questions, failed turns, cancelled turns
    and the free answer (see gets_free_answer) don't.
    """
    return ChatMessage.objects.filter(
        user_id=user_id,
        role=ChatMessage.Role.ASSISTANT,
        status=ChatMessage.Status.SUCCESS,
        free_answer=False,
    ).count()


def count_anon_messages(user_id: str) -> int:
    """Questions a logged-out visitor sent that reached the chat, across all their
    sessions: answered, refused, failed or cancelled. (Sends refused for the limit
    aren't stored, so they aren't counted.) For analytics.
    """
    return ChatMessage.objects.filter(user_id=user_id, role=ChatMessage.Role.USER).count()


def anon_answers_remaining(user_id: str) -> int | None:
    """Answers this logged-out visitor has left, or None when there's no limit."""
    limit = get_anon_answer_limit()
    if limit is None:
        return None
    return max(limit - count_anon_answers(user_id), 0)


# Questions the host page asks for the visitor (the context's `source`) that can be free
FREE_ANSWER_SOURCES = {"search_no_results"}


def gets_free_answer(user_id: str, source: str, remaining: int | None) -> bool:
    """Whether this question's answer won't use up a free answer.

    The question the empty-search page asks never uses up a visitor's last free
    answer, once per visitor: with one left it's answered for free, and they still
    have one. Any other time it counts like any other question. `remaining` is
    anon_answers_remaining(user_id), which the caller has already worked out.
    """
    if source not in FREE_ANSWER_SOURCES or remaining != 1:
        return False
    return not ChatMessage.objects.filter(user_id=user_id, free_answer=True).exists()
