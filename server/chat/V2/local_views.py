"""Endpoints serving the local agent runner.

See docs/plans/2026-09-08-local-agent-runner.md. These exist so a daemon on a
user's machine can run the agent without holding any server secret.
"""

import json
import logging

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from ..auth.auth_service import (
    AuthenticationRequired,
    InvalidUserToken,
    UserTokenExpired,
    authenticate_request,
)
from .guardrail import get_guardrail_service
from .prompts import get_prompt_service
from .router import get_router_service
from .summarization.summary_service import SUMMARY_PROMPT

logger = logging.getLogger("chat")


@api_view(["POST"])
def local_identity(request):
    """Resolve an encrypted userId token into the identity a runner stores.

    The runner calls this once, at pairing: decrypting the token needs
    CHATBOT_USER_TOKEN_SECRET, which must not leave the server. Requires a valid
    token, so it tells a caller nothing they did not already hold.

    POST /api/v2/local/identity
    """
    try:
        actor = authenticate_request(request, request.data)
    except UserTokenExpired:
        return Response({"error": "userId_expired"}, status=status.HTTP_401_UNAUTHORIZED)
    except (InvalidUserToken, AuthenticationRequired) as exc:
        logger.warning(f"local identity rejected: {exc}")
        return Response({"error": "invalid_userId"}, status=status.HTTP_401_UNAUTHORIZED)

    return Response({"userId": actor.user_id, "sefariaUserId": actor.sefaria_user_id})


def _actor_or_error(request):
    """Authenticate, returning either an Actor or an error Response."""
    try:
        return authenticate_request(request, request.data)
    except UserTokenExpired:
        return Response({"error": "userId_expired"}, status=status.HTTP_401_UNAUTHORIZED)
    except (InvalidUserToken, AuthenticationRequired) as exc:
        logger.warning(f"local request rejected: {exc}")
        return Response({"error": "invalid_userId"}, status=status.HTTP_401_UNAUTHORIZED)


@api_view(["POST"])
def local_prompt(request):
    """Serve one Braintrust prompt to a runner.

    Proxied per fetch rather than shipped as a bundle: the router picks the core
    prompt at request time, so the runner cannot know in advance which prompts a
    turn will need. Keeps prompt updates, versioning and A/B tests central.

    POST /api/v2/local/prompt
    """
    actor = _actor_or_error(request)
    if isinstance(actor, Response):
        return actor

    prompt_id = request.data.get("promptId")
    build_vars = request.data.get("buildVars") or {}
    version = request.data.get("version") or "stable"

    prompt = get_prompt_service().get_core_prompt(
        prompt_id=prompt_id,
        version=version,
        build_vars=build_vars,
    )
    return Response({"text": prompt.text, "promptId": prompt.prompt_id, "version": prompt.version})


@api_view(["POST"])
def local_guardrail(request):
    """Run the guardrail for a runner.

    Stays server-side deliberately: it is a brand-safety control, and a check the
    user's own machine could edit would not be one.

    POST /api/v2/local/guardrail
    """
    actor = _actor_or_error(request)
    if isinstance(actor, Response):
        return actor

    message = request.data.get("message") or ""
    result = get_guardrail_service().check_message(message)
    return Response({"allowed": result.allowed, "reason": result.reason})


@api_view(["POST"])
def local_route(request):
    """Classify a message for a runner.

    POST /api/v2/local/route
    """
    actor = _actor_or_error(request)
    if isinstance(actor, Response):
        return actor

    message = request.data.get("message") or ""
    result = get_router_service().classify(message)
    return Response(
        {
            "route": result.route.value,
            "corePromptId": result.core_prompt_id,
            "rewrittenMessage": result.rewritten_message,
        }
    )


@api_view(["POST"])
def local_summary(request):
    """Generate one conversation summary for a runner.

    Only the model call moves: the runner applies the returned fields to its own
    database, so local history stays local (D4). Summaries matter more than they
    sound like they do — the agent receives only the current message, so this is
    the entire multi-turn memory.

    Returns the parsed summary fields, or 502 if the model output was unusable,
    which the runner treats as a signal to fall back to rule-based extraction.

    POST /api/v2/local/summary
    """
    actor = _actor_or_error(request)
    if isinstance(actor, Response):
        return actor

    context_parts = []
    previous = request.data.get("previousSummary")
    if previous:
        context_parts.append(f"Previous Summary:\n{previous}")
    context_parts.append(f"User: {(request.data.get('userMessage') or '')[:1000]}")
    context_parts.append(f"Assistant: {(request.data.get('assistantResponse') or '')[:1000]}")

    from django.conf import settings

    from .pricing import tracked_messages_create
    from .utils import get_anthropic_client, strip_markdown_fences

    try:
        response = tracked_messages_create(
            get_anthropic_client(),
            model=settings.SUMMARY_MODEL,
            max_tokens=500,
            temperature=0.0,
            system=SUMMARY_PROMPT,
            messages=[{"role": "user", "content": "\n\n".join(context_parts)}],
        )
        data = json.loads(strip_markdown_fences(response.content[0].text))
    except (json.JSONDecodeError, IndexError) as exc:
        logger.warning(f"local summary unparseable: {exc}")
        return Response({"error": "summary_unavailable"}, status=status.HTTP_502_BAD_GATEWAY)
    except Exception as exc:
        logger.error(f"local summary failed: {exc}")
        return Response({"error": "summary_unavailable"}, status=status.HTTP_502_BAD_GATEWAY)

    return Response({"summary": data})
