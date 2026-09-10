"""The daemon's HTTP surface.

Starlette rather than Django: this process has no models, no settings and no
views to reuse, so a framework would only bring back the inheritance that made
the previous design fragile.

The security model is carried over unchanged, because it was the part that
worked: loopback only, a pairing code that proves terminal access, a bearer
token on every route but ``/health`` and ``/pair``, and an origin allowlist.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from . import config, history, pairing, session
from .agent import Conversations

logger = logging.getLogger("local_agent")

EXEMPT_PATHS = frozenset({"/health", "/pair"})

# The daemon binds loopback, so a request naming any other host was routed here
# by a name that resolves to 127.0.0.1 — the shape of a DNS-rebinding attack.
LOOPBACK_HOSTS = ["127.0.0.1", "localhost"]
BEARER_PREFIX = "Bearer "

DEFAULT_SYSTEM_PROMPT = (
    "You are Sefaria's Library Assistant. Answer questions about Jewish texts "
    "using the Sefaria tools available to you. Before sending a response that "
    "contains links, call validate_response_links on your draft and fix "
    "anything it reports."
)


def _unauthorized(reason: str) -> JSONResponse:
    return JSONResponse({"error": "runner_unauthorized", "reason": reason}, status_code=401)


def _presented_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if not header.startswith(BEARER_PREFIX):
        return None
    return header[len(BEARER_PREFIX) :].strip() or None


async def health(request: Request) -> JSONResponse:
    """Answers "is an agent here" before the page holds a token."""
    return JSONResponse(
        {"status": "ok", "mode": "local-agent", "paired": pairing.load() is not None}
    )


async def pair(request: Request) -> JSONResponse:
    """Exchange the code printed in this terminal for a bearer token."""
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    if not session.pairing_open():
        return JSONResponse({"error": "pairing_closed"}, status_code=409)

    encrypted_user_token = body.get("userId")
    if not encrypted_user_token:
        return JSONResponse({"error": "missing_userId"}, status_code=400)

    # Verified but not yet spent, so a failure below leaves the code usable.
    if not session.verify(body.get("code")):
        logger.warning("pairing rejected: wrong or spent code")
        return JSONResponse({"error": "invalid_code"}, status_code=403)

    identity = await _resolve_identity(encrypted_user_token)
    if identity is None:
        return JSONResponse({"error": "identity_unresolved"}, status_code=502)

    record = pairing.pair(
        user_id=identity["userId"],
        sefaria_user_id=identity.get("sefariaUserId"),
        encrypted_user_token=encrypted_user_token,
    )
    session.spend()
    logger.info("paired with sefaria user %s", identity.get("sefariaUserId") or "(anonymous)")

    return JSONResponse({"runnerToken": record.runner_token})


async def _resolve_identity(encrypted_user_token: str) -> dict | None:
    """Ask Sefaria who this token belongs to; the daemon cannot decrypt it."""
    url = f"{config.chatbot_url()}/api/v2/local/identity"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json={"userId": encrypted_user_token})
    except httpx.HTTPError as exc:
        logger.warning("could not reach %s: %s", url, exc)
        return None

    if response.status_code != 200:
        logger.warning("identity rejected by the server (%s)", response.status_code)
        return None
    return response.json()


async def _system_prompt(encrypted_user_token: str) -> str:
    """Fetch Sefaria's prompt, falling back to a built-in one.

    Central prompts still matter — they are how the local agent sounds like the
    hosted one — but an unreachable server should degrade the voice, not the
    turn.
    """
    url = f"{config.chatbot_url()}/api/v2/local/prompt"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(url, json={"userId": encrypted_user_token})
        if response.status_code == 200:
            text = response.json().get("text")
            if text:
                return text
    except httpx.HTTPError as exc:
        logger.warning("using the built-in prompt (%s unreachable): %s", url, exc)
    return DEFAULT_SYSTEM_PROMPT


async def _guardrail_allows(encrypted_user_token: str, message: str) -> tuple[bool, str]:
    """Run Sefaria's guardrail. Fails closed: unchecked is not allowed."""
    url = f"{config.chatbot_url()}/api/v2/local/guardrail"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                url, json={"userId": encrypted_user_token, "message": message}
            )
    except httpx.HTTPError as exc:
        logger.warning("guardrail unavailable, blocking: %s", exc)
        return False, "I can't check that message right now. Please try again in a moment."

    if response.status_code != 200:
        logger.warning("guardrail unavailable (%s), blocking", response.status_code)
        return False, "I can't check that message right now. Please try again in a moment."

    data = response.json()
    return bool(data.get("allowed")), data.get("reason", "")


def _final_payload(
    message_id: str,
    session_id: str,
    answer: str,
    tool_calls: list[dict],
    turn_count: int,
) -> dict:
    """The final message, in the exact shape the widget reads.

    Two fields are easy to get wrong and both break the UI rather than degrade
    it. The answer is ``markdown``, not ``text``. And the id must differ from
    the user message's, because the transcript is a keyed list — reusing it
    gives two entries the same key and the render fails outright.
    """
    return {
        "messageId": f"{message_id}-response" if message_id else "",
        "sessionId": session_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "markdown": answer,
        "traceId": None,
        "toolCalls": tool_calls,
        "session": {"turnCount": turn_count},
        "stats": {},
        "recovered": False,
        "status": "success",
    }


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


async def chat_stream(request: Request) -> StreamingResponse:
    """Run one turn, streaming the events the widget already renders."""
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    record = pairing.load()
    if record is None:
        return JSONResponse({"error": "not_paired"}, status_code=401)

    text = (body.get("text") or "").strip()
    session_id = body.get("sessionId") or "default"
    message_id = body.get("messageId") or ""
    page_url = body.get("pageUrl") or ""

    conversations: Conversations = request.app.state.conversations

    async def stream() -> AsyncIterator[str]:
        started = time.time()

        allowed, reason = await _guardrail_allows(record.encrypted_user_token, text)
        if not allowed:
            yield _sse("message", _final_payload(message_id, session_id, reason, [], 0))
            return

        yield _sse("progress", {"type": "status", "text": "Thinking..."})

        conversation = conversations.get(session_id)
        if conversation.system_prompt is None:
            conversation.system_prompt = await _system_prompt(record.encrypted_user_token)

        answer = ""
        try:
            async for event_name, payload in conversation.ask(text):
                if payload.get("type") == "complete":
                    answer = payload.get("text", "")
                    continue
                yield _sse(event_name, payload)
        except Exception as exc:  # noqa: BLE001 - the turn must always end cleanly
            logger.exception("turn failed")
            yield _sse("error", {"error": str(exc)})
            return

        conversation.turn_count += 1
        yield _sse(
            "message",
            _final_payload(
                message_id,
                session_id,
                answer,
                (conversation.last_result.tool_calls if conversation.last_result else []),
                conversation.turn_count,
            ),
        )

        if message_id:
            history.save_turn(
                encrypted_user_token=record.encrypted_user_token,
                session_id=session_id,
                message_id=message_id,
                user_text=text,
                assistant_text=answer,
                latency_ms=int((time.time() - started) * 1000),
                page_url=page_url,
            )

    return StreamingResponse(stream(), media_type="text/event-stream")


async def history_proxy(request: Request) -> JSONResponse:
    """Serve chat history from Sefaria.

    Turns are recorded server-side (see history.save_turn), so the transcript
    lives there, not here. The widget points every call at this daemon while
    local mode is on, so without this the history request 404s — and the
    widget's infinite-scroll loader retries a failed load indefinitely.
    """
    record = pairing.load()
    if record is None:
        return JSONResponse({"error": "not_paired"}, status_code=401)

    params = dict(request.query_params)
    params["userId"] = record.encrypted_user_token
    url = f"{config.chatbot_url()}/api/history"

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(url, params=params)
    except httpx.HTTPError as exc:
        logger.warning("history unavailable (%s): %s", url, exc)
        # An empty page rather than an error: a failed history load must not
        # leave the widget retrying.
        return JSONResponse({"messages": [], "hasMore": False})

    if response.status_code != 200:
        logger.warning("history unavailable: %s returned %s", url, response.status_code)
        return JSONResponse({"messages": [], "hasMore": False})

    return JSONResponse(response.json())


class RunnerAuth:
    """Bearer and origin checks, in front of every route but the exempt two."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)

        origin = request.headers.get("origin")
        if origin and origin not in config.allowed_origins():
            _report_origin_once(origin)
            await _unauthorized("origin not allowed")(scope, receive, send)
            return

        if request.url.path not in EXEMPT_PATHS and not pairing.token_matches(
            _presented_token(request)
        ):
            await _unauthorized("missing or invalid runner token")(scope, receive, send)
            return

        await self.app(scope, receive, send)


_reported_origins: set[str] = set()


def _report_origin_once(origin: str) -> None:
    """Name a refused origin once, with the way to allow it."""
    if origin in _reported_origins:
        return
    _reported_origins.add(origin)
    logger.warning(
        "Refused a request from %s: not an allowed origin. "
        "To allow it, restart with SEFARIA_ALLOWED_ORIGINS=%s",
        origin,
        origin,
    )


def build_app() -> Starlette:
    app = Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/pair", pair, methods=["POST"]),
            Route("/api/history", history_proxy, methods=["GET"]),
            Route("/api/chat/stream", chat_stream, methods=["POST"]),
            Route("/api/v2/chat/stream", chat_stream, methods=["POST"]),
        ],
        middleware=[
            # Rejects a Host header naming anything but loopback, which is what
            # stops a rebound DNS name from reaching the daemon. Django gave us
            # this via ALLOWED_HOSTS; Starlette needs it asked for.
            Middleware(TrustedHostMiddleware, allowed_hosts=LOOPBACK_HOSTS),
            Middleware(
                CORSMiddleware,
                allow_origins=config.allowed_origins(),
                allow_methods=["GET", "POST", "OPTIONS"],
                allow_headers=["Authorization", "Content-Type"],
            ),
            Middleware(RunnerAuth),
        ],
    )
    app.state.conversations = Conversations()
    return app
