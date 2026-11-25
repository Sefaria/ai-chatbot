"""Sefaria refs in normal form, as far as findings need them. No Django, no network.

Our service has no copy of Sefaria's library, so it cannot expand a ref itself. Sefaria does,
on every sheet save: a sheet's ``expandedRefs`` are the segments its sources cover, in normal
form. What is left to do here is to say which cited source each of those segments belongs to,
so that it gets that source's role, and which section each segment is in, which is what the
reader asks about. Both work on the normal form's address: the last space-separated part of a
ref (``Shulchan Arukh, Orach Chayim 1:1`` → ``1:1``; ``Berakhot 2a:3-5`` → ``2a:3-5``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

ROLE_RANK = {"anchor": 0, "position": 1, "support": 2}
SIDEBAR_ROLES = ("anchor", "position")

_PART = r"\d+[ab]?"
_POINT = rf"{_PART}(?::{_PART})*"
_ADDRESS = re.compile(rf"^(?P<start>{_POINT})(?:-(?P<end>{_POINT}))?$")
_URL_FORM = re.compile(
    rf"^(?P<title>.+?)\.(?P<address>{_PART}(?:\.{_PART})*(?:-{_PART}(?:\.{_PART})*)?)$"
)


def split(ref: str) -> tuple[str, str | None]:
    """``(title, address)``; the address is None when the ref names a whole book or node."""
    ref = (ref or "").strip()
    title, _, address = ref.rpartition(" ")
    if title and _ADDRESS.match(address):
        return title, address
    return ref, None


def _key(part: str) -> int:
    """Sortable value of one address part: ``12`` → 24, ``2a`` → 4, ``2b`` → 5."""
    if part[-1] in "ab":
        return int(part[:-1]) * 2 + (part[-1] == "b")
    return int(part) * 2


def _point(text: str) -> tuple[int, ...]:
    return tuple(_key(p) for p in text.split(":"))


def span(ref: str) -> tuple[str, tuple[int, ...], tuple[int, ...]] | None:
    """``(title, start, end)`` of a ref, or None if it has no address.

    A shortened end takes its missing leading parts from the start: ``1:3-5`` ends at ``1:5``.
    """
    title, address = split(ref)
    if address is None:
        return None
    m = _ADDRESS.match(address)
    start = _point(m["start"])
    end = _point(m["end"]) if m["end"] else start
    if len(end) < len(start):
        end = start[: len(start) - len(end)] + end
    return title, start, end


def contains(outer: str, inner: str) -> bool:
    """Whether segment (or range) ``inner`` lies within ``outer``.

    ``Genesis 1`` contains ``Genesis 1:5``; ``Genesis 1:30-2:2`` contains ``Genesis 2:1``.
    """
    o, i = span(outer), span(inner)
    if o is None:
        return split(outer)[0] == (i[0] if i else split(inner)[0])
    if i is None or o[0] != i[0]:
        return False
    depth = len(o[1])
    if len(i[1]) < depth:
        return False
    return o[1] <= i[1][:depth] and i[2][:depth] <= o[2]


def section_of(ref: str) -> str:
    """The section a segment is in: ``Genesis 1:5`` → ``Genesis 1``; ``Berakhot 2a:3`` → ``Berakhot 2a``.

    A ref with a one-part address is its own section.
    """
    title, address = split(ref)
    if address is None or ":" not in address or "-" in address:
        return ref.strip()
    return f"{title} {address.rsplit(':', 1)[0]}"


RANGE_SEGMENTS_MAX = 500


def segments_in(ref: str) -> list[str]:
    """The segments of a range within one section (``Genesis 1:3-5`` → 1:3, 1:4, 1:5).

    Empty for anything else (a single segment, a section, a range across sections), which
    the caller matches by section or segment instead.
    """
    title, address = split(ref)
    if address is None or "-" not in address:
        return []
    start, _, end = address.partition("-")
    head, _, first = start.rpartition(":")
    if not head or not first.isdigit():
        return []
    last = end.rpartition(":")[2] if ":" in end else end
    if ":" in end and end.rpartition(":")[0] != head:
        return []
    if not last.isdigit() or not int(first) <= int(last) <= int(first) + RANGE_SEGMENTS_MAX:
        return []
    return [f"{title} {head}:{n}" for n in range(int(first), int(last) + 1)]


def book_of(ref: str) -> str:
    return split(ref)[0]


def from_url(ref: str) -> str:
    """Accept a ref as a URL path segment: ``Genesis.1.5`` or ``Shulchan_Arukh,_Orach_Chayim.1.1``."""
    ref = (ref or "").strip().replace("_", " ")
    if split(ref)[1] is not None:
        return ref
    m = _URL_FORM.match(ref)
    if not m:
        return ref
    return f"{m['title']} {m['address'].replace('.', ':')}"


def to_url(ref: str) -> str:
    """The reader's URL form of a ref: ``Genesis 1:30-2:2`` → ``Genesis.1.30-2.2``."""
    title, address = split(ref)
    path = title.replace(" ", "_")
    return f"{path}.{address.replace(':', '.')}" if address else path


def sort_key(ref: str):
    s = span(ref)
    return (s[0], s[1]) if s else (ref, ())


def rows_for(
    roles: Sequence[dict], expanded: Iterable[str], included: Iterable[str] | None = None
) -> list[tuple[str, str, str, str]]:
    """``(source_ref, segment_ref, section_ref, role)`` for every segment a finding touches.

    ``roles`` are the cited sources as ``{"ref", "role"}``; ``expanded`` is the sheet's
    ``expandedRefs``. A segment covered by several sources takes the strongest role (anchor,
    then position, then support). A segment no cited source covers is support, attributed
    to itself. A cited source that matches no segment, such as a ref Sefaria expands in a way
    this parser cannot follow, still gets a row of its own so its role is not lost, as long as
    the sheet still holds it (``included``, the sheet's ``includedRefs``; None means trust it).
    """
    sources = sorted(
        (r for r in roles if r.get("ref") and r.get("role") in ROLE_RANK),
        key=lambda r: ROLE_RANK[r["role"]],
    )
    rows: list[tuple[str, str, str, str]] = []
    matched: set[str] = set()
    for segment in sorted(set(expanded), key=sort_key):
        source = next((s for s in sources if contains(s["ref"], segment)), None)
        if source:
            matched.add(source["ref"])
            rows.append((source["ref"], segment, section_of(segment), source["role"]))
        else:
            rows.append((segment, segment, section_of(segment), "support"))
    seen = {row[1] for row in rows}
    still_cited = None if included is None else set(included)
    for source in sources:
        if still_cited is not None and source["ref"] not in still_cited:
            continue
        if source["ref"] not in matched and source["ref"] not in seen:
            seen.add(source["ref"])
            rows.append((source["ref"], source["ref"], section_of(source["ref"]), source["role"]))
    return rows
