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

# Shown in the summary of every sheet the tool creates, so the owner can tell Claude made it.
SHEET_ATTRIBUTION = "Created with Claude."


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


def attributed_summary(summary: str) -> str:
    """The sheet summary, ending with a note that Claude made the sheet."""
    summary = summary.strip()
    if summary.endswith(SHEET_ATTRIBUTION):
        return summary
    return f"{summary}\n\n{SHEET_ATTRIBUTION}" if summary else SHEET_ATTRIBUTION


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
        "summary": attributed_summary(summary),
        "sources": normalized_sources,
        "nextNode": next_node,
        "options": deepcopy(DEFAULT_SHEET_OPTIONS),
        "topics": [],
        "tags": [],
        "displayedCollection": "",
    }


def merge_source_sheet_sources(
    existing_sources: list[dict[str, Any]], changes: list[dict[str, Any]], next_node: int
) -> tuple[list[dict[str, Any]], int]:
    """Apply an update's source list to a sheet's existing sources.

    ``changes`` is the sheet's new source list, in order. Each item is one of:
    ``{"node": n}`` keeps existing source n as it is; ``{"node": n, "outsideText": ...}``
    replaces the text of an existing outside-text source; ``{"node": n, "ref", "heRef"}``
    replaces existing source n with that ref; an item without ``node`` adds a new source
    (``outsideText``, or ``ref`` plus ``heRef``). Existing sources left out are removed,
    except media sources: Sefaria deletes an image's file when its source is removed, so
    those must be kept. New and replaced ref sources come back without ``text``, to be
    filled in by the caller. Returns the new sources and the sheet's next free node.
    """
    if not isinstance(changes, list) or not changes:
        raise ValueError("update_source_sheet needs at least one source; omit sources to keep them")

    by_node = {
        source.get("node"): source for source in existing_sources if isinstance(source, dict)
    }
    merged: list[dict[str, Any]] = []
    seen: set[int] = set()

    for change in changes:
        if not isinstance(change, dict):
            raise ValueError("Each source must be an object")
        node = change.get("node")
        if node is None:
            new_source = prepare_source_sheet_sources([change])[0]
            new_source["node"] = next_node
            next_node += 1
            merged.append(new_source)
            continue

        if node not in by_node:
            raise ValueError(f"The sheet has no source with node {node}")
        if node in seen:
            raise ValueError(f"Source node {node} is listed twice")
        seen.add(node)
        existing = by_node[node]

        if "ref" in change:
            if "media" in existing:
                raise ValueError(f"Source {node} is an image or media source and can't be replaced")
            replacement = prepare_source_sheet_sources([{**change, "node": node}])[0]
            replacement.pop("text", None)
            merged.append(replacement)
        elif "outsideText" in change:
            if "outsideText" not in existing:
                raise ValueError(
                    f"Source {node} isn't an outside-text source; replace it with a ref instead"
                )
            text = change["outsideText"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError("outsideText must be a non-empty string")
            merged.append({**deepcopy(existing), "outsideText": text})
        else:
            merged.append(deepcopy(existing))

    removed_media = [n for n, source in by_node.items() if n not in seen and "media" in source]
    if removed_media:
        raise ValueError(
            "Image and media sources can't be removed (Sefaria would delete the uploaded file): "
            f"keep node(s) {', '.join(map(str, sorted(removed_media)))} in the list"
        )
    return merged, next_node
