"""Endpoints serving the local agent runner.

See docs/plans/2026-09-08-local-agent-runner.md. These exist so a daemon on a
user's machine can run the agent without holding any server secret.
"""

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
