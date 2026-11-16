"""The "From your profile, I took ... into account" line. The answering model writes it inside a
`<profile_used>` tag; the orchestrator splits it off so the tag never reaches storage or the user."""

from __future__ import annotations

import re

TAG_OPEN = "<profile_used>"
TAG_CLOSE = "</profile_used>"
MAX_LENGTH = 300
LINE_OPENINGS = ("From your profile", "Nothing from your profile")

_CLOSED = re.compile(r"<profile_used>(.*?)</profile_used>", re.DOTALL)
_STRAY_CLOSE = re.compile(r"</profile_used>")
_MARKUP = re.compile(r"<[^<>]*>")


def _valid_line(inner: str) -> str | None:
    line = inner.strip()
    if not line.startswith(LINE_OPENINGS) or "<" in line or ">" in line:
        return None
    return line[:MAX_LENGTH]


def split_profile_used(text: str) -> tuple[str, str | None]:
    """Remove every `<profile_used>...</profile_used>`, any unclosed `<profile_used>` through the
    end, and any stray closing tag. Each removed span leaves one space, so the text on either
    side never joins into new markup. Returns (clean text, the last closed tag's line when it
    starts with "From your profile" or "Nothing from your profile", has no angle brackets and
    is at most 300 characters; otherwise None)."""
    line = None
    for match in _CLOSED.finditer(text):
        if match.group(1).strip():
            line = _valid_line(match.group(1))
    clean = _CLOSED.sub(" ", text)
    cut = clean.find(TAG_OPEN)
    if cut != -1:
        clean = clean[:cut] + " "
    clean = _STRAY_CLOSE.sub(" ", clean)
    return clean.strip(), line


def wrapped_answer(text: str) -> str:
    """The text of an answer the model wrapped entirely in the tag: the tags and any other markup
    and every angle bracket removed, so it can be shown as the answer."""
    stripped = _MARKUP.sub("", text)
    return stripped.replace("<", "").replace(">", "").strip()
