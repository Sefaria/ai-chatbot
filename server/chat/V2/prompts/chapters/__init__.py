"""Prompt chapters: Markdown files read from disk, so they ship in the image."""

from __future__ import annotations

import hashlib
from functools import cache
from pathlib import Path

_DIR = Path(__file__).parent


@cache
def load_chapter(name: str) -> str:
    """The text of `<name>.md` in this folder. Raises FileNotFoundError for an unknown name."""
    if "/" in name or "\\" in name or name.startswith("."):
        raise ValueError(f"invalid chapter name: {name!r}")
    return (_DIR / f"{name}.md").read_text(encoding="utf-8")


def chapter_sha(name: str) -> str:
    """The first 12 hex characters of the sha256 of the chapter text."""
    return hashlib.sha256(load_chapter(name).encode("utf-8")).hexdigest()[:12]
