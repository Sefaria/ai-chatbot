"""Prompt chapters: Braintrust by slug when configured, else Markdown files bundled in the image."""

from __future__ import annotations

import hashlib
import logging
import time
from functools import cache
from pathlib import Path

from django.conf import settings

logger = logging.getLogger("chat.prompts")

_DIR = Path(__file__).parent
_BACKOFF_SECONDS = 60.0

# chapter name -> the settings attribute holding its Braintrust slug (empty = bundled)
_SLUG_SETTINGS = {
    "personalization_answering": "PERSONALIZATION_PROMPT_SLUG_ANSWERING",
    "personalization_profile_used": "PERSONALIZATION_PROMPT_SLUG_PROFILE_USED",
    "profile_writer": "PERSONALIZATION_PROMPT_SLUG_PROFILE_WRITER",
    "profile_extraction": "PERSONALIZATION_PROMPT_SLUG_PROFILE_EXTRACTION",
    "profile_dream": "PERSONALIZATION_PROMPT_SLUG_PROFILE_DREAM",
    "profile_edit": "PERSONALIZATION_PROMPT_SLUG_PROFILE_EDIT",
}

# chapter name -> monotonic time of its last Braintrust failure
_failed_at: dict[str, float] = {}


@cache
def _bundled(name: str) -> str:
    """The text of `<name>.md` in this folder. Raises FileNotFoundError for an unknown name."""
    if "/" in name or "\\" in name or name.startswith("."):
        raise ValueError(f"invalid chapter name: {name!r}")
    return (_DIR / f"{name}.md").read_text(encoding="utf-8")


def chapter_sha(name: str) -> str:
    """The first 12 hex characters of the sha256 of the bundled chapter text."""
    return hashlib.sha256(_bundled(name).encode("utf-8")).hexdigest()[:12]


def _resolve(name: str) -> tuple[str, str]:
    """(text, source) for a chapter: Braintrust when its slug is set and the fetch works."""
    slug = getattr(settings, _SLUG_SETTINGS.get(name, ""), "") if name in _SLUG_SETTINGS else ""
    if slug:
        failed = _failed_at.get(name)
        if failed is None or time.monotonic() - failed >= _BACKOFF_SECONDS:
            try:
                from ..prompt_service import get_prompt_service

                prompt = get_prompt_service().get_core_prompt(prompt_id=slug)
                _failed_at.pop(name, None)
                return prompt.text, f"braintrust:{slug}@{prompt.version}"
            except Exception as exc:
                _failed_at[name] = time.monotonic()
                logger.warning(
                    "Chapter %s: Braintrust prompt %r unavailable (%s); using bundled file",
                    name,
                    slug,
                    exc,
                )
    return _bundled(name), f"bundled@{chapter_sha(name)}"


def load_chapter(name: str) -> str:
    """The chapter text: from Braintrust when its slug is set and reachable, else bundled."""
    return _resolve(name)[0]


def chapter_source(name: str) -> str:
    """`braintrust:<slug>@<version>` or `bundled@<sha12>`: what load_chapter returns right now."""
    return _resolve(name)[1]
