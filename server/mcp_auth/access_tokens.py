"""Access tokens for /mcp/user and /mcp/admin: short-lived HS256 JWTs.

Issued by the Django authorization server and verified statelessly by the MCP process
(fastmcp's ``JWTVerifier``), which has no database. Both hold ``MCP_OAUTH_SIGNING_KEY``.
No Django imports.
"""

from __future__ import annotations

import time
import uuid

import jwt

ALGORITHM = "HS256"
ACCESS_TOKEN_TTL_SECONDS = 15 * 60
SCOPE = "sheets"  # /mcp/user
ADMIN_SCOPE = "admin"  # /mcp/admin


def issue(
    *,
    signing_key: str,
    issuer: str,
    audience: str,
    grant_id: str,
    sefaria_user_id: int,
    client_id: str,
    scope: str = SCOPE,
    ttl_seconds: int = ACCESS_TOKEN_TTL_SECONDS,
) -> str:
    now = int(time.time())
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": grant_id,
        "sefaria_user_id": int(sefaria_user_id),
        "client_id": client_id,
        "scope": scope,
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, signing_key, algorithm=ALGORITHM)
