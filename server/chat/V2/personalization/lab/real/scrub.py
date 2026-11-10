"""Anonymization primitives: fresh pseudonyms, deterministic PII scrub, LLM name pass, and residual scan."""

from __future__ import annotations

import re
import secrets
from typing import TYPE_CHECKING

from ...parsing import parse_json_object

if TYPE_CHECKING:
    from ...llm import LLMClient

DEFAULT_SCRUB_MODEL = "claude-haiku-4-5-20251001"

NAME_SYSTEM_PROMPT = (
    "You are an anonymization filter for Jewish-texts study conversations. "
    'Return a JSON object {"spans": ["..."]} listing exact substrings in the input that are '
    "names of private persons (learners, relatives, friends, colleagues). "
    "Do NOT include rabbis, biblical or historical figures, classical commentators, authors, or book titles. "
    "Reply with one JSON object only."
)

_URL_QUERY_RE = re.compile(r"https?://[^\s<>\"'\)\]]+\?[^\s<>\"'\)\]]*[^\s<>\"'\)\].,;:!?]")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(
    r"(?<!\w)(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)|\d{2,4})[\s.-]\d{3,4}[\s.-]\d{4}(?!\w)"
)
_NUMBER_RE = re.compile(r"\b\d{6,}\b")

_DETERMINISTIC_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("url", _URL_QUERY_RE, "[url]"),
    ("email", _EMAIL_RE, "[email]"),
    ("phone", _PHONE_RE, "[phone]"),
    ("number", _NUMBER_RE, "[number]"),
)


class Pseudonymizer:
    """In-memory-only random pseudonym table created fresh on each pull."""

    def __init__(self) -> None:
        self._users: dict[str, str] = {}
        self._sessions: dict[tuple[str, str], str] = {}

    def user_id(self, raw_user_id: str) -> str:
        if raw_user_id not in self._users:
            self._users[raw_user_id] = f"u-{secrets.token_hex(8)}"
        return self._users[raw_user_id]

    def session_id(self, raw_user_id: str, raw_session_id: str) -> str:
        key = (raw_user_id, raw_session_id)
        if key not in self._sessions:
            self._sessions[key] = f"s-{secrets.token_hex(8)}"
        return self._sessions[key]


def scrub_text(text: str) -> tuple[str, dict[str, int]]:
    """Replace URLs with query strings, emails, phone numbers, and 6+ digit runs."""
    hits: dict[str, int] = {"email": 0, "phone": 0, "url": 0, "number": 0}
    out = text
    for kind, pattern, replacement in _DETERMINISTIC_PATTERNS:
        out, count = pattern.subn(replacement, out)
        hits[kind] += count
    return out, hits


def residual_hits(text: str) -> int:
    """Re-run deterministic patterns over output text; returns total residual match count."""
    return sum(len(pattern.findall(text)) for _, pattern, _ in _DETERMINISTIC_PATTERNS)


def scrub_names(
    text: str,
    llm: LLMClient,
    *,
    model: str = DEFAULT_SCRUB_MODEL,
) -> tuple[str, int, bool]:
    """Identify private person names via `llm` and replace each span with `[name]`.

    Returns `(scrubbed_text, replacement_count, ok)`. A malformed LLM reply sets `ok=False`.
    """
    try:
        response = llm.complete(
            system=NAME_SYSTEM_PROMPT,
            user=text,
            model=model,
            max_tokens=300,
        )
    except Exception:
        return text, 0, False

    parsed = parse_json_object(response.text)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("spans"), list):
        return text, 0, False
    spans = parsed["spans"]
    if any(not isinstance(span, str) for span in spans):
        return text, 0, False

    out = text
    replaced = 0
    unique_spans = sorted({s.strip() for s in spans if s and s.strip()}, key=len, reverse=True)
    for span in unique_spans:
        if span in ("[email]", "[phone]", "[url]", "[number]", "[name]"):
            continue
        count = out.count(span)
        if count > 0:
            out = out.replace(span, "[name]")
            replaced += count
    return out, replaced, True
