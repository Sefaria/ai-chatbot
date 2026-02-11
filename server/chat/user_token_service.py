"""
User token decryption utilities for chat endpoints.
"""

import base64
import hashlib
import json
from datetime import datetime

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.utils import timezone

NONCE_SIZE_BYTES = 12
MAX_USER_ID_LENGTH = 100


class UserTokenError(ValueError):
    """Raised when a user token is invalid or missing required fields."""


class UserTokenExpiredError(UserTokenError):
    """Raised when a user token is expired."""


def _derive_key(secret: str) -> bytes:
    return hashlib.sha256(secret.encode("utf-8")).digest()


def _urlsafe_b64decode(token: str) -> bytes:
    if not token:
        raise UserTokenError("missing token")

    padding = (-len(token)) % 4
    if padding:
        token = f"{token}{'=' * padding}"

    try:
        return base64.urlsafe_b64decode(token.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise UserTokenError("invalid base64") from exc


def _parse_expiration(expiration: str) -> datetime:
    try:
        expires_at = datetime.fromisoformat(expiration)
    except ValueError as exc:
        raise UserTokenError("invalid expiration") from exc

    if timezone.is_naive(expires_at):
        expires_at = timezone.make_aware(expires_at, timezone.utc)

    return expires_at


def decrypt_chatbot_user_token(
    token: str,
    secret: str,
    now: datetime | None = None,
) -> str:
    if not secret:
        raise UserTokenError("missing secret")

    token_bytes = _urlsafe_b64decode(token)
    if len(token_bytes) <= NONCE_SIZE_BYTES:
        raise UserTokenError("invalid token length")

    nonce = token_bytes[:NONCE_SIZE_BYTES]
    encrypted = token_bytes[NONCE_SIZE_BYTES:]

    aesgcm = AESGCM(_derive_key(secret))
    try:
        payload_bytes = aesgcm.decrypt(nonce, encrypted, None)
    except Exception as exc:  # cryptography raises InvalidTag on failure
        raise UserTokenError("invalid token") from exc

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise UserTokenError("invalid payload") from exc

    user_id = payload.get("id")
    expiration = payload.get("expiration")
    if not user_id or not expiration:
        raise UserTokenError("missing fields")

    if len(str(user_id)) > MAX_USER_ID_LENGTH:
        raise UserTokenError("user id too long")

    expires_at = _parse_expiration(expiration)
    if (now or timezone.now()) > expires_at:
        raise UserTokenExpiredError("token expired")

    return str(user_id)
