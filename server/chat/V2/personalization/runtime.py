"""Process-level plumbing for the request path: what personalization adds to a turn. Nothing here
may fail a chat turn."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PersonalizationContext:
    """The blocks a turn's prompt gets from personalization, plus its trace metadata."""

    instructions_text: str | None = None
    profile_text: str | None = None
    meta: dict = field(default_factory=dict)
    notice: bool = False


def context_for_request(user_id: str, *, plain: bool) -> PersonalizationContext:
    """What to add to this turn's prompt. Empty in this version: the blocks arrive with the
    injection step. `plain=True` (the plain arm of the dual display) never returns blocks."""
    return PersonalizationContext()


def queue_job(user_id: str, trigger: str) -> None:
    """Queue a Profile job on the background pool. A no-op until the job exists."""
    return None
