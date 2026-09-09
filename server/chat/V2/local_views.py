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
