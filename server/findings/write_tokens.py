"""Short-lived tokens the MCP process sends when it records a finding for a signed-in user.

The MCP process knows who the user is from their verified access token, but has no database.
It records a finding by calling the Django app's ``POST /api/findings`` with one of these: an
HS256 JWT signed with ``MCP_OAUTH_SIGNING_KEY`` (which both processes already hold), for the
``findings`` audience only, naming the Sefaria user, valid for five minutes. The user's own
access token is never passed on. No Django imports.
"""

from __future__ import annotations

import time
import uuid

import jwt

ALGORITHM = "HS256"
AUDIENCE = "findings"
TTL_SECONDS = 5 * 60


def mint(user_id: int, signing_key: str, issuer: str, *, ttl_seconds: int = TTL_SECONDS) -> str:
    now = int(time.time())
    claims = {
        "iss": issuer,
        "aud": AUDIENCE,
        "sefaria_user_id": int(user_id),
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, signing_key, algorithm=ALGORITHM)


def user_id_from(token: str, signing_key: str, issuer: str) -> int | None:
    """The Sefaria user id in a valid token, or None."""
    if not token or not signing_key:
        return None
    try:
        claims = jwt.decode(
            token, signing_key, algorithms=[ALGORITHM], audience=AUDIENCE, issuer=issuer
        )
        user_id = int(claims["sefaria_user_id"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        return None
    return user_id if user_id > 0 else None
