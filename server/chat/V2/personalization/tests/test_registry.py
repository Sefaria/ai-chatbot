from chat.V2.personalization.registry import SlotRegistry


def test_v1_has_the_six_slots(registry):
    assert registry.version == "v1"
    assert [s.key for s in registry.all()] == [
        "preferred_language",
        "hebrew_fluency",
        "terminology_comfort",
        "text_categories",
        "answer_style",
        "learning_habits",
    ]


def test_thresholds_shelf_life_and_deterministic(registry):
    mins = {s.key: s.min_sessions for s in registry.all()}
    assert mins == {
        "preferred_language": 2,
        "hebrew_fluency": 2,
        "terminology_comfort": 2,
        "text_categories": 1,
        "answer_style": 1,
        "learning_habits": 3,
    }
    assert {s.key for s in registry.all() if s.shelf_life_days} == {"text_categories"}
    assert registry.get("text_categories").shelf_life_days == 60
    assert [s.key for s in registry.all() if s.deterministic] == ["preferred_language"]


def test_get_unknown_slot_is_none(registry):
    assert registry.get("nope") is None
    assert isinstance(SlotRegistry.load(), SlotRegistry)


def test_validate_enum(registry):
    assert registry.validate("hebrew_fluency", "basic") == "basic"
    assert registry.validate("hebrew_fluency", "expert") is None
    assert registry.validate("hebrew_fluency", ["basic"]) is None


def test_validate_list_filters_dedupes_and_truncates(registry):
    value = ["Talmud", "Pizza", "Talmud", "Mishnah", "Midrash", "Musar"]
    assert registry.validate("text_categories", value) == ["Talmud", "Mishnah", "Midrash"]
    assert registry.validate("text_categories", ["Pizza"]) is None
    assert registry.validate("text_categories", []) is None
    assert registry.validate("text_categories", "Talmud") == ["Talmud"]


def test_validate_text_strips_single_line_and_truncates(registry):
    assert registry.validate("answer_style", "  Keep it\nshort.  ") == "Keep it short."
    assert registry.validate("answer_style", "   ") is None
    assert registry.validate("answer_style", 5) is None
    assert len(registry.validate("learning_habits", "x" * 500)) == 200


def test_validate_unknown_slot(registry):
    assert registry.validate("nope", "x") is None
