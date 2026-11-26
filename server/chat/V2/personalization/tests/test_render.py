from chat.V2.personalization.render import render_profile
from chat.V2.personalization.types import SlotValue

from .conftest import NOW, day


def slot(key, value, status="inferred", expires_at=None):
    return SlotValue(key, value, status, [], NOW, expires_at)


def test_empty_profile_renders_nothing(registry):
    assert render_profile({}, registry, NOW) == ("", [])


def test_one_line_per_slot_in_registry_order_with_stated_marker(registry):
    profile = {
        "answer_style": slot("answer_style", "Keep answers short."),
        "text_categories": slot("text_categories", ["Talmud", "Musar"]),
        "hebrew_fluency": slot("hebrew_fluency", "basic", "stated"),
    }
    text, used = render_profile(profile, registry, NOW)
    assert text.splitlines() == [
        "- Hebrew reading level: basic (set by the learner)",
        "- Texts they study: Talmud, Musar",
        "- Preferred answer style: Keep answers short.",
    ]
    assert used == ["hebrew_fluency", "text_categories", "answer_style"]


def test_expired_slots_are_skipped(registry):
    profile = {"text_categories": slot("text_categories", ["Talmud"], expires_at=day(-1))}
    assert render_profile(profile, registry, NOW) == ("", [])


def test_budget_drops_slots_from_the_end(registry):
    profile = {
        "hebrew_fluency": slot("hebrew_fluency", "basic"),
        "answer_style": slot("answer_style", "x" * 200),
        "learning_habits": slot("learning_habits", "y" * 200),
    }
    text, used = render_profile(profile, registry, NOW, max_chars=260)
    assert used == ["hebrew_fluency", "answer_style"]
    assert len(text) <= 260
    text, used = render_profile(profile, registry, NOW, max_chars=40)
    assert used == ["hebrew_fluency"]


def test_budget_smaller_than_first_line_renders_nothing(registry):
    profile = {"hebrew_fluency": slot("hebrew_fluency", "basic")}
    assert render_profile(profile, registry, NOW, max_chars=5) == ("", [])
