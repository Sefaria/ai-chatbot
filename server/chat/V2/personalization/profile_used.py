"""The "From your profile, I took ... into account" line. The answering model ends its answer with
the line inside a `<profile_used>` tag; the orchestrator takes the tag off the end.

Only a tag at the very end of the text is taken off, so the remaining text is always a prefix of
what the model wrote: nothing is cut out of the middle, and no text can join into new markup. A tag
anywhere else, or an unclosed one, stays as ordinary model output (the widget sanitizes it)."""

from __future__ import annotations

import re

NOTHING_LINE = "Nothing from your profile changed this answer."

_OPEN = r"<\s*profile_used\s*>"
_CLOSE = r"<\s*/\s*profile_used\s*>"
# The last tag that ends the text: its inner text holds no other opening or closing tag.
_TRAILING = re.compile(
    rf"{_OPEN}((?:(?!{_OPEN}|{_CLOSE}).)*){_CLOSE}\s*\Z", re.IGNORECASE | re.DOTALL
)
_ANY_TAG = re.compile(rf"{_OPEN}|{_CLOSE}", re.IGNORECASE)
_FROM_LINE = re.compile(r"From your profile, I took [^\r\n<>]{1,250} into account\.")


def valid_line(inner: str) -> str | None:
    """The line when it is one of the two agreed shapes, else None."""
    line = inner.strip()
    if line == NOTHING_LINE or _FROM_LINE.fullmatch(line):
        return line
    return None


def split_profile_used(text: str) -> tuple[str, str | None]:
    """Take every `<profile_used>...</profile_used>` off the end of the text (case-insensitive,
    spaces allowed inside the tags) and return (the text before them, rstripped; the line of the
    last of them whose inner text is one of the two agreed shapes, or None). A text that does not
    end with a closed tag is returned unchanged."""
    body = text
    line = None
    while (match := _TRAILING.search(body)) is not None:
        if line is None:
            line = valid_line(match.group(1))
        body = body[: match.start()].rstrip()
    if body == text:
        return text, None
    return body, line


def only_tags(text: str) -> bool:
    """True when the text holds nothing but `<profile_used>` tags and whitespace."""
    return not _ANY_TAG.sub("", text).strip()
