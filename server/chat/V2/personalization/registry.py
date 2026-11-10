"""Slot registry: slot definitions are data (slots.json), not code."""

from __future__ import annotations

import json
from pathlib import Path

from .types import SlotDef, Value

_SLOTS_PATH = Path(__file__).parent / "slots.json"


class SlotRegistry:
    def __init__(self, version: str, slots: list[SlotDef]):
        self.version = version
        self._slots = {slot.key: slot for slot in slots}

    @classmethod
    def load(cls, path: Path | None = None) -> SlotRegistry:
        data = json.loads((path or _SLOTS_PATH).read_text(encoding="utf-8"))
        slots = []
        for raw in data["slots"]:
            raw = {**raw, "allowed": tuple(raw.get("allowed", ()))}
            slots.append(SlotDef(**raw))
        return cls(data["version"], slots)

    def get(self, key: str) -> SlotDef | None:
        return self._slots.get(key)

    def all(self) -> list[SlotDef]:
        return list(self._slots.values())

    def validate(self, key: str, value: object) -> Value | None:
        """Return the normalized value, or None when it is not acceptable for the slot."""
        slot = self.get(key)
        if slot is None:
            return None
        if slot.value_type == "enum":
            return value if isinstance(value, str) and value in slot.allowed else None
        if slot.value_type == "list":
            items = [value] if isinstance(value, str) else value
            if not isinstance(items, list):
                return None
            kept = list(dict.fromkeys(i for i in items if isinstance(i, str) and i in slot.allowed))
            if slot.max_items:
                kept = kept[: slot.max_items]
            return kept or None
        if not isinstance(value, str):
            return None
        text = " ".join(value.split())
        if slot.max_chars:
            text = text[: slot.max_chars].rstrip()
        return text or None
