"""Render a learner profile as prompt text, under a hard character budget."""

from __future__ import annotations

from datetime import datetime

from .registry import SlotRegistry
from .types import SlotValue

DEFAULT_MAX_CHARS = 1600


def _format_value(value: str | list[str]) -> str:
    return ", ".join(value) if isinstance(value, list) else value


def render_profile(
    profile: dict[str, SlotValue],
    registry: SlotRegistry,
    now: datetime,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> tuple[str, list[str]]:
    """One line per filled, unexpired slot; returns (text, slot keys used)."""
    lines: list[str] = []
    keys: list[str] = []
    for slot in registry.all():
        current = profile.get(slot.key)
        if current is None or (current.expires_at and current.expires_at <= now):
            continue
        suffix = " (set by the learner)" if current.status == "stated" else ""
        lines.append(f"- {slot.label}: {_format_value(current.value)}{suffix}")
        keys.append(slot.key)
    while lines and len("\n".join(lines)) > max_chars:
        lines.pop()
        keys.pop()
    return "\n".join(lines), keys
