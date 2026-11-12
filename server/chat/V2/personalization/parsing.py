"""Tolerant parsing of model output, and the quote normalization used for evidence checks."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter

_BRACKETS = str.maketrans({"<": "‹", ">": "›"})


def clean_text(value: str) -> str:
    """Make free text safe to place in a prompt: no control characters, no line breaks, no tags.

    Whitespace and control characters collapse to single spaces (or vanish), and angle brackets
    become single guillemets, so a value can never close a prompt block or open a tag.
    """
    chars = []
    for ch in value:
        if ch.isspace() or unicodedata.category(ch) in ("Zl", "Zp"):
            chars.append(" ")
        elif unicodedata.category(ch) != "Cc":
            chars.append(ch)
    return " ".join("".join(chars).translate(_BRACKETS).split())


_DECODER = json.JSONDecoder()
# A bare double quote between two Hebrew letters is a gershayim inside an acronym (רשב"ם).
_HEBREW_ACRONYM_QUOTE = re.compile(r"([֐-׿])\"(?=[֐-׿])")


def _braces(text: str) -> list[int]:
    return [i for i, ch in enumerate(text) if ch == "{"]


def _object_at(text: str, start: int) -> dict | None:
    try:
        value, _ = _DECODER.raw_decode(text, start)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def parse_json_object(text: str, diagnostics: Counter | None = None) -> dict | None:
    """Return the first JSON object in `text`, ignoring markdown fences and surrounding prose.

    Candidates are tried outermost first (by position). For each one, a plain parse is tried, then
    one repair (escape a bare double quote inside a Hebrew acronym), counted as `reply_repaired`;
    only then does the scan move on to the next brace, so an outer envelope is not lost to a
    nested object.
    """
    if not text:
        return None
    repaired = _HEBREW_ACRONYM_QUOTE.sub(r'\1\\"', text)
    # The repair adds no braces, so the n-th brace of one text is the n-th of the other.
    for plain_at, repaired_at in zip(_braces(text), _braces(repaired), strict=True):
        if (parsed := _object_at(text, plain_at)) is not None:
            return parsed
        if repaired != text and (parsed := _object_at(repaired, repaired_at)) is not None:
            if diagnostics is not None:
                diagnostics["reply_repaired"] += 1
            return parsed
    return None


_QUOTE_MAP = str.maketrans({"״": '"', "׳": "'", "“": '"', "”": '"', "‘": "'", "’": "'"})


def normalize_quote(text: str) -> str:
    """NFC, straight quotes, collapsed whitespace: what both sides of a quote check go through."""
    return " ".join(unicodedata.normalize("NFC", text).translate(_QUOTE_MAP).split())


def locate_quote(quote: str, text: str) -> str | None:
    """The learner's original substring of `text` that matches `quote` after normalization.

    Returns None when the quote is empty or does not occur. The returned substring is taken from
    the NFC form of `text`, so a quote with different quote marks or spacing is stored as the
    learner wrote it.
    """
    wanted = normalize_quote(quote)
    if not wanted:
        return None
    source = unicodedata.normalize("NFC", text)
    chars: list[str] = []
    origin: list[int] = []  # index in `source` of each normalized char
    for i, ch in enumerate(source):
        if ch.isspace():
            if chars and chars[-1] != " ":
                chars.append(" ")
                origin.append(i)
            continue
        chars.append(ch.translate(_QUOTE_MAP))
        origin.append(i)
    haystack = "".join(chars)
    at = haystack.find(wanted)
    if at < 0:
        return None
    return source[origin[at] : origin[at + len(wanted) - 1] + 1]


def validate_envelope(
    reply: object,
    required: dict[str, type],
    optional: dict[str, type] | None = None,
    *,
    allow_extra: bool = False,
) -> dict | None:
    """The reply when it is a dict with every required key of the right type, else None.

    `optional` keys may be absent but must have the right type when present. Unknown keys fail
    unless `allow_extra`. A bool is never accepted for `int` or `float`, and `{}` fails whenever
    something is required.
    """
    if not isinstance(reply, dict):
        return None
    allowed = {**(optional or {}), **required}

    def fits(value: object, kind: type) -> bool:
        if kind is object:
            return True
        if kind is bool:
            return isinstance(value, bool)
        return isinstance(value, kind) and not isinstance(value, bool)

    if any(key not in reply or not fits(reply[key], kind) for key, kind in required.items()):
        return None
    if any(key in reply and not fits(reply[key], kind) for key, kind in (optional or {}).items()):
        return None
    if not allow_extra and set(reply) - set(allowed):
        return None
    return reply
