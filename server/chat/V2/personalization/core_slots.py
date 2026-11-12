"""The eight core Profile slots: a stable key and the heading the user sees."""

from __future__ import annotations

CORE_SLOTS: list[tuple[str, str]] = [
    ("reply_language", "Reply language"),
    ("hebrew_reading", "Hebrew reading"),
    ("terminology_comfort", "Learning terminology"),
    ("library_areas", "Parts of the library you know"),
    ("current_interests", "Current interests"),
    ("commentators", "Commentators you seek out"),
    ("answer_style", "Kind of answers you want"),
    ("learning_rhythm", "Learning rhythm"),
]

CORE_KEYS: tuple[str, ...] = tuple(key for key, _ in CORE_SLOTS)
CORE_HEADINGS: dict[str, str] = dict(CORE_SLOTS)
