"""Runner-only endpoints."""

from __future__ import annotations

import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import identity, pairing
from .session import consume, pairing_open

logger = logging.getLogger("local_runner")


def health(request):
    """Liveness probe the widget uses to decide whether local mode is available.

    Unauthenticated on purpose: it answers "is a runner here", nothing more, and
    the widget needs an answer before it has presented a token.
    """
    return JsonResponse(
        {
            "status": "ok",
            "mode": "local",
            "paired": pairing.load() is not None,
        }
    )


@csrf_exempt
@require_POST
def pair(request):
    """Exchange the code printed in this daemon's terminal for a runner token.

    Presenting the code is the proof: only someone who can see this machine's
    terminal has it. It is single use and time limited (see ``session``). The
    userId token is forwarded to our server once, to resolve the identity stored
    in the pairing record.
    """
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "invalid_json"}, status=400)

    if not pairing_open():
        return JsonResponse({"error": "pairing_closed"}, status=409)

    encrypted_user_token = body.get("userId")
    if not encrypted_user_token:
        return JsonResponse({"error": "missing_userId"}, status=400)

    # Spends the code: a second attempt with the same one is refused, and a run
    # of wrong guesses closes pairing until the daemon is restarted.
    if not consume(body.get("code")):
        logger.warning("pairing rejected: wrong or spent code")
        return JsonResponse({"error": "invalid_code"}, status=403)

    try:
        resolved = identity.resolve(encrypted_user_token)
    except identity.IdentityError as exc:
        logger.warning("pairing failed: %s", exc)
        return JsonResponse({"error": "identity_unresolved", "reason": str(exc)}, status=502)

    record = pairing.pair(
        user_id=resolved.user_id,
        sefaria_user_id=resolved.sefaria_user_id,
        encrypted_user_token=encrypted_user_token,
    )
    logger.info("paired with sefaria user %s", resolved.sefaria_user_id or "(anonymous)")

    return JsonResponse({"runnerToken": record.runner_token})
