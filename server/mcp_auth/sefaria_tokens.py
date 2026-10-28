"""Sefaria's encrypted user tokens, as minted by ``sefaria.utils.chatbot.build_chatbot_user_token``.

AES-GCM over ``{"id", "user_id", "expiration", "aud"}`` with a key derived from a shared
secret. For MCP the secret is ``MCP_TOKEN_SECRET`` and the audience is either
``mcp-login`` (Sefaria -> us, proving who signed in) or ``mcp`` (us -> Sefaria, per tool
call). No Django imports: the MCP process uses ``mint``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

LOGIN_AUDIENCE = "mcp-login"
API_AUDIENCE = "mcp"
API_TOKEN_TTL = timedelta(minutes=5)
NONCE_SIZE_BYTES = 12


def _key(secret: str) -> bytes:
    return hashlib.sha256(secret.encode("utf-8")).digest()


def mint(
    user_id: int,
    secret: str,
    *,
    aud: str = API_AUDIENCE,
    ttl: timedelta = API_TOKEN_TTL,
    state: str | None = None,
) -> str:
    expires_at = (datetime.now(UTC) + ttl).replace(microsecond=0)
    payload = {
        "id": hashlib.sha256(str(user_id).encode("utf-8")).hexdigest(),
        "user_id": int(user_id),
        "expiration": expires_at.isoformat(),
        "aud": aud,
    }
    if state:
        payload["state"] = state
    nonce = os.urandom(NONCE_SIZE_BYTES)
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(nonce + AESGCM(_key(secret)).encrypt(nonce, data, None)).decode(
        "ascii"
    )


def user_id_from(token: str, secret: str, *, aud: str, state: str | None = None) -> int | None:
    """The Sefaria user id in ``token``, or None if it is invalid, expired or for another audience.

    With ``state``, the token must also have been issued for that sign-in flow.
    """
    if not token or not secret:
        return None
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        if len(raw) <= NONCE_SIZE_BYTES:
            return None
        nonce, encrypted = raw[:NONCE_SIZE_BYTES], raw[NONCE_SIZE_BYTES:]
        payload = json.loads(AESGCM(_key(secret)).decrypt(nonce, encrypted, None))
        expires_at = datetime.fromisoformat(payload["expiration"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if payload.get("aud") != aud or expires_at <= datetime.now(UTC):
            return None
        if state is not None and payload.get("state") != state:
            return None
        user_id = int(payload["user_id"])
        return user_id if user_id > 0 else None
    except Exception:  # malformed base64/JSON, wrong key (InvalidTag), missing fields
        return None
