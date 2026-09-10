"""Posting finished turns to sefaria.org, so chat history is not local-only.

The daemon keeps no database. Conversations belong in the same tables the hosted
chat uses, which is what makes them readable from the Library Assistant and from
another device — the thing a local SQLite file could never give.

Best effort by design: a turn the user has already seen must not fail because
history could not be written, so a failure here is logged and dropped.
"""

from __future__ import annotations

import logging

import httpx

from . import config

logger = logging.getLogger("local_agent")

TURN_PATH = "/api/v2/local/turn"
TIMEOUT_SECONDS = 15


def save_turn(
    *,
    encrypted_user_token: str,
    session_id: str,
    message_id: str,
    user_text: str,
    assistant_text: str,
    latency_ms: int | None = None,
    page_url: str = "",
) -> bool:
    """Record one completed turn. Returns whether it was stored."""
    if not config.history_enabled():
        return False

    url = f"{config.chatbot_url()}{TURN_PATH}"
    payload = {
        "userId": encrypted_user_token,
        "sessionId": session_id,
        "messageId": message_id,
        "userText": user_text,
        "assistantText": assistant_text,
        "latencyMs": latency_ms,
        "pageUrl": page_url,
    }

    try:
        response = httpx.post(url, json=payload, timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        logger.warning("history not saved (%s unreachable): %s", url, exc)
        return False

    if response.status_code != 200:
        logger.warning("history not saved: %s returned %s", TURN_PATH, response.status_code)
        return False

    return True
