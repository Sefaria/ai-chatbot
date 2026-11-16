"""The "From your profile, I took ... into account" line. The answering model writes it inside a
`<profile_used>` tag; the orchestrator splits it off so the tag never reaches storage or the user."""

from __future__ import annotations

import re

TAG_OPEN = "<profile_used>"
MAX_LENGTH = 300

_CLOSED = re.compile(r"<profile_used>(.*?)</profile_used>", re.DOTALL)


def split_profile_used(text: str) -> tuple[str, str | None]:
    """Remove every `<profile_used>...</profile_used>` and any unclosed `<profile_used>` through
    the end. Returns (clean text, the last closed tag's inner text stripped, at most 300
    characters, or None when there is none)."""
    line = None
    for match in _CLOSED.finditer(text):
        inner = match.group(1).strip()
        if inner:
            line = inner[:MAX_LENGTH]
    clean = _CLOSED.sub("", text)
    cut = clean.find(TAG_OPEN)
    if cut != -1:
        clean = clean[:cut]
    return clean.strip(), line
