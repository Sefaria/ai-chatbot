"""Tests for user token decryption service."""

import base64
import hashlib
import json
from datetime import datetime, timedelta

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.utils import timezone

from chat.user_token_service import (
    UserTokenError,
    UserTokenExpiredError,
    decrypt_chatbot_user_token,
)


def create_test_token(
    user_id: str,
    secret: str,
    expires_at: datetime | None = None,
    payload_override: dict | None = None,
) -> str:
    """Create a valid encrypted token for testing."""
    if expires_at is None:
        expires_at = timezone.now() + timedelta(hours=1)

    payload = {"id": user_id, "expiration": expires_at.isoformat()}
    if payload_override:
        payload.update(payload_override)

    payload_bytes = json.dumps(payload).encode("utf-8")

    key = hashlib.sha256(secret.encode("utf-8")).digest()
    aesgcm = AESGCM(key)
    nonce = b"\x00" * 12  # Fixed nonce for deterministic tests
    encrypted = aesgcm.encrypt(nonce, payload_bytes, None)

    token_bytes = nonce + encrypted
    return base64.urlsafe_b64encode(token_bytes).decode("ascii").rstrip("=")


class TestDecryptChatbotUserToken:
    """Tests for decrypt_chatbot_user_token function."""

    @pytest.fixture
    def secret(self):
        return "test-secret-key-12345"

    def test_valid_token_returns_user_id(self, secret):
        """Valid token should decrypt and return user ID."""
        token = create_test_token("user_123", secret)
        result = decrypt_chatbot_user_token(token, secret)
        assert result == "user_123"

    def test_valid_token_with_numeric_user_id(self, secret):
        """Token with numeric user ID should work."""
        token = create_test_token("12345", secret)
        result = decrypt_chatbot_user_token(token, secret)
        assert result == "12345"

    def test_expired_token_raises_error(self, secret):
        """Expired token should raise UserTokenExpiredError."""
        expired_time = timezone.now() - timedelta(hours=1)
        token = create_test_token("user_123", secret, expires_at=expired_time)

        with pytest.raises(UserTokenExpiredError, match="expired"):
            decrypt_chatbot_user_token(token, secret)

    def test_wrong_secret_raises_error(self, secret):
        """Token encrypted with different secret should fail."""
        token = create_test_token("user_123", secret)

        with pytest.raises(UserTokenError, match="invalid token"):
            decrypt_chatbot_user_token(token, "wrong-secret")

    def test_missing_token_raises_error(self, secret):
        """Empty token should raise error."""
        with pytest.raises(UserTokenError, match="missing token"):
            decrypt_chatbot_user_token("", secret)

    def test_missing_secret_raises_error(self):
        """Missing secret should raise error."""
        with pytest.raises(UserTokenError, match="missing secret"):
            decrypt_chatbot_user_token("some-token", "")

    def test_invalid_base64_raises_error(self, secret):
        """Invalid base64 should raise error."""
        with pytest.raises(UserTokenError, match="invalid base64"):
            decrypt_chatbot_user_token("not!valid@base64", secret)

    def test_truncated_token_raises_error(self, secret):
        """Token too short to contain nonce raises error."""
        short_token = base64.urlsafe_b64encode(b"short").decode("ascii")
        with pytest.raises(UserTokenError, match="invalid token length"):
            decrypt_chatbot_user_token(short_token, secret)

    def test_corrupted_ciphertext_raises_error(self, secret):
        """Corrupted ciphertext should raise error."""
        # Create valid token, then corrupt it
        token = create_test_token("user_123", secret)
        token_bytes = base64.urlsafe_b64decode(token + "==")
        corrupted = token_bytes[:-5] + b"XXXXX"
        corrupted_token = base64.urlsafe_b64encode(corrupted).decode("ascii").rstrip("=")

        with pytest.raises(UserTokenError, match="invalid token"):
            decrypt_chatbot_user_token(corrupted_token, secret)

    def test_missing_user_id_in_payload_raises_error(self, secret):
        """Payload without user ID should raise error."""
        token = create_test_token("user_123", secret, payload_override={"id": None})

        with pytest.raises(UserTokenError, match="missing fields"):
            decrypt_chatbot_user_token(token, secret)

    def test_missing_expiration_in_payload_raises_error(self, secret):
        """Payload without expiration should raise error."""
        # Create token with missing expiration
        payload = {"id": "user_123"}
        payload_bytes = json.dumps(payload).encode("utf-8")
        key = hashlib.sha256(secret.encode("utf-8")).digest()
        aesgcm = AESGCM(key)
        nonce = b"\x00" * 12
        encrypted = aesgcm.encrypt(nonce, payload_bytes, None)
        token = base64.urlsafe_b64encode(nonce + encrypted).decode("ascii").rstrip("=")

        with pytest.raises(UserTokenError, match="missing fields"):
            decrypt_chatbot_user_token(token, secret)

    def test_user_id_too_long_raises_error(self, secret):
        """User ID exceeding max length should raise error."""
        long_user_id = "x" * 101
        token = create_test_token(long_user_id, secret)

        with pytest.raises(UserTokenError, match="user id too long"):
            decrypt_chatbot_user_token(token, secret)

    def test_token_valid_at_exact_expiration(self, secret):
        """Token at exact expiration time should still be expired."""
        now = timezone.now()
        token = create_test_token("user_123", secret, expires_at=now)

        # Token expires at 'now', so checking at 'now' + tiny delta should fail
        with pytest.raises(UserTokenExpiredError):
            decrypt_chatbot_user_token(token, secret, now=now + timedelta(seconds=1))

    def test_naive_datetime_expiration_treated_as_utc(self, secret):
        """Naive datetime in expiration should be treated as UTC."""
        # Create token with naive datetime
        future = datetime.utcnow() + timedelta(hours=1)  # noqa: DTZ003
        payload = {"id": "user_123", "expiration": future.isoformat()}
        payload_bytes = json.dumps(payload).encode("utf-8")
        key = hashlib.sha256(secret.encode("utf-8")).digest()
        aesgcm = AESGCM(key)
        nonce = b"\x00" * 12
        encrypted = aesgcm.encrypt(nonce, payload_bytes, None)
        token = base64.urlsafe_b64encode(nonce + encrypted).decode("ascii").rstrip("=")

        # Should work - naive datetime treated as UTC
        result = decrypt_chatbot_user_token(token, secret)
        assert result == "user_123"
