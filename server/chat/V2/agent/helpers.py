"""Shared helper utilities for the V2 Claude agent runtime."""

from __future__ import annotations

from typing import Any


def extract_refs(tool_calls: list[dict[str, Any]]) -> list[str]:
    """Extract unique Sefaria refs from tool calls for Braintrust logging."""
    seen = set()
    refs: list[str] = []
    for tool_call in tool_calls:
        ref = tool_call.get("tool_input", {}).get("reference")
        if ref and ref not in seen:
            seen.add(ref)
            refs.append(ref)
    return refs


def truncate(text: str, max_len: int) -> str:
    """Truncate text to max length, appending '...' if trimmed."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
