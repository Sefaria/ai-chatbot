"""OAuth state for /mcp/user. Every secret is stored as a SHA-256 hash, never in clear."""

import hashlib
import secrets
import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone

AUTHORIZATION_REQUEST_TTL = timedelta(minutes=10)
AUTHORIZATION_CODE_TTL = timedelta(seconds=60)
REFRESH_TOKEN_IDLE_TTL = timedelta(days=30)
GRANT_ABSOLUTE_TTL = timedelta(days=90)


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def secret_matches(value: str | None, stored_hash: str) -> bool:
    return bool(value and stored_hash) and secrets.compare_digest(hash_secret(value), stored_hash)


class OAuthClient(models.Model):
    """A client registered through dynamic client registration (RFC 7591). Public: no secret."""

    client_id = models.CharField(max_length=64, unique=True, default=new_secret)
    client_name = models.CharField(max_length=200, blank=True)
    redirect_uris = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self) -> str:
        return self.client_name or self.client_id


class AuthorizationRequest(models.Model):
    """One run of /oauth/authorize, from the redirect to Sefaria until its code is redeemed.

    ``id`` is the state sent to Sefaria. ``browser_binding_hash`` ties the flow to the
    browser that started it, so a flow cannot be completed from someone else's browser.
    """

    id = models.CharField(primary_key=True, max_length=64, default=new_secret)
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE)
    redirect_uri = models.TextField()
    client_state = models.TextField(blank=True)
    code_challenge = models.CharField(max_length=128)
    scope = models.CharField(max_length=200)
    browser_binding_hash = models.CharField(max_length=64)
    consent_token_hash = models.CharField(max_length=64, blank=True)
    sefaria_user_id = models.BigIntegerField(null=True)
    code_hash = models.CharField(max_length=64, null=True, unique=True)
    code_expires_at = models.DateTimeField(null=True)
    grant = models.ForeignKey("Grant", null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    @property
    def expired(self) -> bool:
        return timezone.now() > self.created_at + AUTHORIZATION_REQUEST_TTL


class Grant(models.Model):
    """A user's approval of one client. The refresh token is the long-lived credential."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE)
    sefaria_user_id = models.BigIntegerField(db_index=True)
    scope = models.CharField(max_length=200)
    refresh_token_hash = models.CharField(max_length=64, unique=True)
    refresh_expires_at = models.DateTimeField()
    absolute_expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True)

    @property
    def active(self) -> bool:
        now = timezone.now()
        return (
            self.revoked_at is None
            and now < self.refresh_expires_at
            and now < self.absolute_expires_at
        )

    @classmethod
    def for_refresh_token(cls, token: str) -> tuple["Grant | None", bool]:
        """The grant a refresh token belongs to, and whether it is the current one.

        A rotated-out token still identifies its grant (RFC 9700 4.14.2), so its
        replay or revocation can end the whole family.
        """
        token_hash = hash_secret(token or "-")
        grant = cls.objects.select_related("client").filter(refresh_token_hash=token_hash).first()
        if grant is not None:
            return grant, True
        consumed = (
            ConsumedRefreshToken.objects.select_related("grant")
            .filter(token_hash=token_hash)
            .first()
        )
        return (consumed.grant, False) if consumed else (None, False)

    def rotate_refresh_token(self) -> str:
        """Issue a new refresh token, recording the old one to detect its reuse."""
        token = new_secret()
        now = timezone.now()
        ConsumedRefreshToken.objects.create(grant=self, token_hash=self.refresh_token_hash)
        self.refresh_token_hash = hash_secret(token)
        self.refresh_expires_at = min(now + REFRESH_TOKEN_IDLE_TTL, self.absolute_expires_at)
        self.last_used_at = now
        return token

    def revoke(self) -> None:
        if self.revoked_at is None:
            self.revoked_at = timezone.now()
            self.save(update_fields=["revoked_at"])


class ConsumedRefreshToken(models.Model):
    """A refresh token already rotated out of its grant. Presenting one again revokes the grant."""

    grant = models.ForeignKey(Grant, on_delete=models.CASCADE, related_name="consumed_tokens")
    token_hash = models.CharField(max_length=64, unique=True)
