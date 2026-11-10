"""Tolerant parsing of model output, and the quote normalization used for evidence checks."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter

_DECODER = json.JSONDecoder()
# A bare double quote between two Hebrew letters is a gershayim inside an acronym (רשב"ם).
_HEBREW_ACRONYM_QUOTE = re.compile(r"([֐-׿])\"(?=[֐-׿])")


def _first_object(text: str) -> dict | None:
    start = text.find("{")
    while start != -1:
        try:
            value, _ = _DECODER.raw_decode(text, start)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict):
            return value
        start = text.find("{", start + 1)
    return None


def parse_json_object(text: str, diagnostics: Counter | None = None) -> dict | None:
    """Return the first JSON object in `text`, ignoring markdown fences and surrounding prose.

    If nothing parses, try one repair (escape a bare double quote inside a Hebrew acronym) and
    retry; a successful repair is counted as `reply_repaired` in `diagnostics`.
    """
    if not text:
        return None
    parsed = _first_object(text)
    if parsed is not None:
        return parsed
    repaired = _HEBREW_ACRONYM_QUOTE.sub(r'\1\\"', text)
    if repaired != text and (parsed := _first_object(repaired)) is not None:
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
