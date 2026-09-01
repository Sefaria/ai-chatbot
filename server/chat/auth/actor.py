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
    sefaria_user_id: str | None = None

    @property
    def identity(self) -> str:
        """Return the actor's identity string for logging."""
        return self.user_id

    @property
    def user_id_candidates(self) -> list[str]:
        """Return possible persisted ids during the raw-id anonymization rollout."""
        candidates = [self.user_id]
        if self.sefaria_user_id and self.sefaria_user_id not in candidates:
            candidates.append(self.sefaria_user_id)
        return candidates

    def to_db_fields(self) -> dict:
        """Return dict of fields for database model creation."""
        return {"user_id": self.user_id}
