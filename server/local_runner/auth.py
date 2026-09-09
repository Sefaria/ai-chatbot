"""Authentication backend for the local runner.

Installed via ``CHAT_AUTH_BACKEND``, which ``chat.auth.auth_service`` consults
before its own token decryption. The daemon serves exactly one paired user, so
the Actor comes from the pairing record rather than from the request: the runner
token has already established who is calling.
"""

from __future__ import annotations

from chat.auth.actor import Actor
from chat.auth.auth_service import AuthenticationRequired

from . import pairing


def authenticate_paired_request(request, body_data: dict | None = None) -> Actor:
    """Return the Actor this daemon is paired to."""
    record = pairing.load()
    if record is None:
        raise AuthenticationRequired()

    # The encrypted token still travels with the request; the agent layer passes
    # it to Sefaria API calls that act on the user's behalf.
    encrypted_token = (body_data or {}).get("userId")

    return Actor(
        user_id=record.user_id,
        encrypted_token=encrypted_token,
        sefaria_user_id=record.sefaria_user_id,
    )
