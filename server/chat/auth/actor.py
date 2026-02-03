"""
Actor dataclass representing an authenticated identity.

An Actor is either a user (identified by user_id) or a service (identified by service_id).
"""

from dataclasses import dataclass


@dataclass
class Actor:
    """
    Represents an authenticated identity for chat operations.

    Either user_id or service_id must be set, but not both.
    """

    user_id: str | None = None
    service_id: str | None = None

    def __post_init__(self):
        if not self.user_id and not self.service_id:
            raise ValueError("Actor must have user_id or service_id")
        if self.user_id and self.service_id:
            raise ValueError("Actor cannot have both user_id and service_id")

    @property
    def is_service(self) -> bool:
        """Return True if this actor is a service."""
        return self.service_id is not None

    @property
    def is_user(self) -> bool:
        """Return True if this actor is a user."""
        return self.user_id is not None

    @property
    def identity(self) -> str:
        """Return the actor's identity string for logging."""
        if self.service_id:
            return f"service:{self.service_id}"
        return self.user_id

    def to_db_fields(self) -> dict:
        """Return dict of fields for database model creation."""
        return {
            "user_id": self.user_id,
            "service_id": self.service_id,
        }
