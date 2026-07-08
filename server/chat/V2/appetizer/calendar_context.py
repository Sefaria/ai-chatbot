"""Render Sefaria's calendar API payload into a compact, high-signal context block.

Kept minimal on purpose (context rot): only learning-schedule cycles that real
traffic asks for. Add fields only on observed failures.
"""

from __future__ import annotations

# Sefaria calendar item en-title -> compact field name. One canonical field per concept.
_CALENDAR_FIELDS: dict[str, str] = {
    "Parashat Hashavua": "parsha",
    "Haftarah": "haftarah",
    "Daf Yomi": "daf_yomi",
    "Mishnah Yomi": "mishnah_yomi",
    # Both intentionally map to the same field: the Sefaria calendar rotates
    # between the 1-chapter and 3-chapter Rambam cycles, and the two cycles'
    # en-titles differ, but we surface them as a single "rambam_yomi" concept.
    "Daily Rambam": "rambam_yomi",
    "Daily Rambam (3 Chapters)": "rambam_yomi",
    "Yerushalmi Yomi": "yerushalmi_yomi",
    "Tanakh Yomi": "tanakh_yomi",
}


def render_calendar_context(calendar: dict) -> str:
    lines: list[str] = []
    date = (calendar.get("Gregorian Date") or "")[:10]
    if date:
        lines.append(f"date: {date}")

    seen: set[str] = set()
    for item in calendar.get("calendar_items", []):
        en_title = (item.get("title") or {}).get("en", "")
        field = _CALENDAR_FIELDS.get(en_title)
        if not field or field in seen:
            continue
        value = (item.get("displayValue") or {}).get("en", "").strip()
        if not value:
            continue
        seen.add(field)
        lines.append(f"{field}: {value}")

    if not seen:
        return "<calendar_context>unavailable</calendar_context>"

    body = "\n".join(lines)
    return f"<calendar_context>\n{body}\n</calendar_context>"
