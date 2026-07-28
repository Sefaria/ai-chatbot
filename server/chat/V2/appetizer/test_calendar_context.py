from .calendar_context import render_calendar_context


def test_render_includes_known_learning_schedules():
    calendar = {
        "Gregorian Date": "2026-06-25T09:00:00",
        "calendar_items": [
            {"title": {"en": "Parashat Hashavua"}, "displayValue": {"en": "Chukat-Balak"}},
            {"title": {"en": "Haftarah"}, "displayValue": {"en": "Judges 11:1-33"}},
            {"title": {"en": "Daf Yomi"}, "displayValue": {"en": "Chullin 56"}},
            {"title": {"en": "Mishnah Yomi"}, "displayValue": {"en": "Keilim 3:5-6"}},
            {"title": {"en": "Unknown Cycle"}, "displayValue": {"en": "ignore me"}},
        ],
    }
    result = render_calendar_context(calendar)
    assert result.startswith("<calendar_context>")
    assert result.endswith("</calendar_context>")
    assert "date: 2026-06-25" in result
    assert "parsha: Chukat-Balak" in result
    assert "haftarah: Judges 11:1-33" in result
    assert "daf_yomi: Chullin 56" in result
    assert "mishnah_yomi: Keilim 3:5-6" in result
    assert "ignore me" not in result  # unknown titles dropped


def test_render_unavailable_when_no_known_items():
    assert render_calendar_context({"calendar_items": []}) == (
        "<calendar_context>unavailable</calendar_context>"
    )
    assert render_calendar_context({}) == ("<calendar_context>unavailable</calendar_context>")


def test_render_unavailable_when_only_date_present():
    calendar = {"Gregorian Date": "2026-06-25T09:00:00", "calendar_items": []}
    assert render_calendar_context(calendar) == ("<calendar_context>unavailable</calendar_context>")
