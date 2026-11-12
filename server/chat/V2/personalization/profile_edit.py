"""Chat-to-edit: one synchronous model call that changes the Profile, or refuses.

The Settings box sends a request of at most 300 characters. Code refuses the clear cases first
(never-store topics, hiding sources or voices) without a model call; the model handles the rest
and may refuse too. A change goes through the same code checks as a job write and is stored only
if the Profile revision is still the one it was computed from. The quota is taken by the API
handler. Returns ("applied" | "refused", reason); on a refusal nothing changed.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from django.conf import settings
from django.utils import timezone

from chat.V2.prompts.chapters import load_chapter

from .checks import _NEVER_STORE_EXTRA, check_profile, normalize_heading
from .core_slots import CORE_HEADINGS, CORE_KEYS
from .llm import AnthropicLLMClient, LLMClient
from .parsing import clean_text, parse_json_object
from .profile_store import (
    SectionData,
    get_sections,
    get_settings,
    replace_sections,
)
from .signals import is_sensitive

logger = logging.getLogger(__name__)

CHAPTER = "profile_edit"
MAX_REQUEST = 300
MAX_REASON = 200

# Asking to hide, drop or rank sources or voices: that belongs in Instructions.
_HIDE_SOURCES = re.compile(
    r"\b(hide|exclude|block|never\s+show|don'?t\s+show|do\s+not\s+show|stop\s+showing|avoid|"
    r"ignore|never\s+cite|don'?t\s+cite|never\s+use|don'?t\s+use)\b.{0,60}"
    r"\b(sources?|voices?|commentators?|authors?|books?)\b",
    re.IGNORECASE,
)

REASONS = {
    "never_store": "LA does not keep anything about health, family, location, observance or the like.",
    "hide_sources": "Hiding sources or voices belongs in Instructions, where you set the wording.",
    "empty": "Tell LA what to change, in a few words.",
    "bad_reply": "LA could not make that change. Please try again.",
    "changed": "Your Profile changed while LA was working. Please try again.",
    "check": "That change would not pass LA's checks for the Profile.",
}


def _refuse(code: str) -> tuple[str, str]:
    return "refused", REASONS[code]


def _profile_block(sections: list[SectionData]) -> str:
    return (
        "\n\n".join(f"[{s.key or 'dynamic'}] {s.heading}\n{clean_text(s.body)}" for s in sections)
        or "(empty)"
    )


def _entries(parsed: dict) -> list[dict] | None:
    raw = parsed.get("sections")
    if not isinstance(raw, list) or not raw:
        return None
    out = []
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("body"), str):
            return None
        key = entry.get("key")
        if key is not None and key not in CORE_KEYS:
            return None
        heading = CORE_HEADINGS[key] if key else clean_text(str(entry.get("heading") or ""))[:100]
        if not heading or (key is None and is_sensitive(heading)):
            return None
        out.append({"key": key, "heading": heading, "body": clean_text(entry["body"])})
    return out


def _apply(sections: list[SectionData], entries: list[dict], now: datetime) -> list[SectionData]:
    """The sections with the edits in; each edited section is `stated`. An empty body removes."""
    rows = [SectionData(**vars(s)) for s in sections]
    for e in entries:
        norm = normalize_heading(e["heading"])
        match = next((r for r in rows if normalize_heading(r.heading) == norm), None)
        if not e["body"]:
            if match:
                rows.remove(match)
            continue
        if match:
            match.body, match.origin, match.last_supported_at = e["body"], "stated", now
        else:
            rows.append(
                SectionData(
                    e["key"],
                    "core" if e["key"] else "dynamic",
                    e["heading"],
                    e["body"],
                    "stated",
                    now,
                    0,
                )
            )
    order = {k: i for i, k in enumerate(CORE_KEYS)}
    core = sorted((r for r in rows if r.kind == "core"), key=lambda r: order.get(r.key, 99))
    ordered = core + [r for r in rows if r.kind != "core"]
    for i, r in enumerate(ordered):
        r.position = i
    return ordered


def edit_profile(user_id: str, request: str, *, llm: LLMClient | None = None) -> tuple[str, str]:
    text = clean_text(request or "")[:MAX_REQUEST]
    if not text:
        return _refuse("empty")
    if is_sensitive(text) or _NEVER_STORE_EXTRA.search(text):
        return _refuse("never_store")
    if _HIDE_SOURCES.search(text):
        return _refuse("hide_sources")

    llm = llm or AnthropicLLMClient()
    current = get_settings(user_id)
    sections = get_sections(user_id)
    user = f"<profile>\n{_profile_block(sections)}\n</profile>\n<request>{text}</request>"
    try:
        raw = llm.complete(
            system=load_chapter(CHAPTER),
            user=user,
            model=settings.PERSONALIZATION_MODEL,
            max_tokens=900,
            timeout=settings.PERSONALIZATION_MODEL_TIMEOUT,
        ).text
    except Exception as exc:  # a model error: nothing changes
        logger.warning("profile edit failed: %s", type(exc).__name__)
        return _refuse("bad_reply")
    parsed = parse_json_object(raw)
    if not parsed or parsed.get("status") not in ("applied", "refused"):
        return _refuse("bad_reply")
    if parsed["status"] == "refused":
        reason = clean_text(str(parsed.get("reason") or ""))[:MAX_REASON]
        return "refused", reason or REASONS["bad_reply"]
    entries = _entries(parsed)
    if entries is None:
        return _refuse("bad_reply")

    final = _apply(sections, entries, timezone.now())
    if final and (result := check_profile(final, llm)) and not result.ok:
        logger.info("profile edit rejected by check: %s", result.failed)
        if result.failed in ("never_store_terms", "never_store_model"):
            return _refuse("never_store")
        return _refuse("check")
    if not replace_sections(
        user_id,
        current.profile_revision,
        final,
        updated_by="you_la",
        processed_through=None,
        set_notice=False,
    ):
        return _refuse("changed")
    reason = clean_text(str(parsed.get("reason") or ""))[:MAX_REASON]
    return "applied", reason or "Done."
