"""Tolerant parsing of model output."""

from __future__ import annotations

import json

_DECODER = json.JSONDecoder()


def parse_json_object(text: str) -> dict | None:
    """Return the first JSON object in `text`, ignoring markdown fences and surrounding prose."""
    if not text:
        return None
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
