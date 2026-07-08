"""
Actor dataclass representing an authenticated user identity.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Actor:
    """Represents an authenticated user for chat operations."""

    user_id: str
    encrypted_token: str | None = None

    @property
    def identity(self) -> str:
        """Return the actor's identity string for logging."""
        return self.user_id

    def to_db_fields(self) -> dict:
        """Return dict of fields for database model creation."""
        return {"user_id": self.user_id}
