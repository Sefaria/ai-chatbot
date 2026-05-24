"""Helpers for normalizing and serializing source-sheet create payloads."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

DEFAULT_SHEET_OPTIONS = {
    "layout": "stacked",
    "boxed": 0,
    "language": "bilingual",
    "numbered": 0,
    "assignable": 0,
    "divineNames": "noSub",
    "collaboration": "none",
    "highlightMode": 0,
    "langLayout": "heRight",
    "bsd": 0,
}


def prepare_source_sheet_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate source-sheet create input and normalize node assignment."""
    if not isinstance(sources, list) or not sources:
        raise ValueError("create_source_sheet requires at least one source")

    normalized_sources: list[dict[str, Any]] = []
    used_nodes: set[int] = set()

    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("Each source must be an object")

        node = source.get("node")
        if node is not None:
            try:
                node = int(node)
            except (TypeError, ValueError) as exc:
                raise ValueError("Source node must be an integer") from exc
            if node <= 0:
                raise ValueError("Source node must be a positive integer")
            if node in used_nodes:
                raise ValueError(f"Duplicate source node {node}")
            used_nodes.add(node)

        if "outsideText" in source:
            outside_text = source.get("outsideText")
            if not isinstance(outside_text, str) or not outside_text.strip():
                raise ValueError("outsideText sources require a non-empty outsideText string")
            normalized_source = {"outsideText": outside_text}
        elif "ref" in source:
            ref = source.get("ref")
            he_ref = source.get("heRef")
            if not isinstance(ref, str) or not ref.strip():
                raise ValueError("Ref sources require a non-empty ref string")
            if not isinstance(he_ref, str) or not he_ref.strip():
                raise ValueError("Ref sources require a non-empty heRef string")
            normalized_source = {"ref": ref, "heRef": he_ref}
            if isinstance(source.get("text"), dict):
                normalized_source["text"] = {
                    key: value for key, value in source["text"].items() if isinstance(value, str)
                }
        else:
            raise ValueError("Each source must include either outsideText or ref/heRef")

        if node is not None:
            normalized_source["node"] = node
        normalized_sources.append(normalized_source)

    next_node = 1
    for source in normalized_sources:
        if source.get("node") is not None:
            continue
        while next_node in used_nodes:
            next_node += 1
        source["node"] = next_node
        used_nodes.add(next_node)
        next_node += 1

    return normalized_sources


def serialize_source_sheet_payload(
    *, title: str, summary: str, sources: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build the final POST payload for creating a source sheet."""
    if not isinstance(title, str):
        raise ValueError("create_source_sheet requires title to be a string")
    if not isinstance(summary, str):
        raise ValueError("create_source_sheet requires summary to be a string")

    normalized_sources = prepare_source_sheet_sources(sources)
    next_node = max(source["node"] for source in normalized_sources) + 1

    return {
        "status": "unlisted",
        "title": title,
        "summary": summary,
        "sources": normalized_sources,
        "nextNode": next_node,
        "options": deepcopy(DEFAULT_SHEET_OPTIONS),
        "topics": [],
        "tags": [],
        "displayedCollection": "",
    }
