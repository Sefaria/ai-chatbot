"""Process-level plumbing for the request path: what personalization adds to a turn. Nothing here
may fail a chat turn."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.core.cache import cache

from ..prompts import chapters
from . import gate, profile_store
from .parsing import escape_block

logger = logging.getLogger(__name__)

CHAPTER = "personalization_answering"


@dataclass
class PersonalizationContext:
    """The blocks a turn's prompt gets from personalization, plus its trace metadata."""

    instructions_text: str | None = None
    profile_text: str | None = None
    meta: dict = field(default_factory=dict)
    notice: bool = False


_NOTICE_TTL = 3600


def _notice_key(message_id: str) -> str:
    return f"personalization:notice:{message_id}"


def notice_for_message(message_id: str | None) -> bool:
    """Whether the turn that answered this user message showed the one-time notice. Remembered
    in the cache for an hour so the recover path can repeat it after a broken stream."""
    if not message_id:
        return False
    try:
        return cache.get(_notice_key(message_id)) is True
    except Exception:
        return False


def context_for_request(
    user_id: str, *, plain: bool, message_id: str | None = None
) -> PersonalizationContext:
    """What to add to this turn's prompt: the user's Instructions and Profile, each only when its
    switch is on and its text is not empty. `plain=True` (the plain arm of the dual display) never
    returns blocks and never consumes the notice. Any failure gives an unpersonalized turn."""
    if plain or not gate.enabled_for(user_id):
        return PersonalizationContext()
    try:
        settings_row = profile_store.get_settings(user_id)
        instructions = (
            escape_block(settings_row.instructions_text or "")
            if settings_row.instructions_enabled
            else ""
        )
        profile = ""
        if settings_row.profile_enabled:
            profile = escape_block(
                profile_store.render_profile(profile_store.get_sections(user_id))
            )
        if not instructions and not profile:
            return PersonalizationContext()

        notice = profile_store.take_notice(user_id)
        meta = {
            "personalized": True,
            "profile_on": settings_row.profile_enabled,
            "instructions_on": settings_row.instructions_enabled,
            "profile_revision": settings_row.profile_revision,
            "chapter_sha": chapters.chapter_sha(CHAPTER),
            "notice_shown": notice,
        }
        if gate.lab_enabled():
            meta["profile_injected"] = profile
            meta["instructions_injected"] = instructions
        if notice and message_id:
            try:
                cache.set(_notice_key(message_id), True, _NOTICE_TTL)
            except Exception:
                logger.warning("Could not remember the personalization notice", exc_info=True)
        return PersonalizationContext(
            instructions_text=instructions or None,
            profile_text=profile or None,
            meta=meta,
            notice=notice,
        )
    except Exception:
        logger.warning("Personalization failed; answering without it", exc_info=True)
        return PersonalizationContext()


def queue_job(user_id: str, trigger: str) -> None:
    """Queue a Profile job on the background pool. A no-op until the job exists."""
    return None
