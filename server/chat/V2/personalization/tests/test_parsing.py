from collections import Counter

from chat.V2.personalization.parsing import locate_quote, normalize_quote, parse_json_object


def test_plain_json():
    assert parse_json_object('{"a": 1}') == {"a": 1}


def test_markdown_fence_and_prose():
    text = 'Sure! Here you go:\n```json\n{"updates": [{"slot": "x"}]}\n```\nHope that helps.'
    assert parse_json_object(text) == {"updates": [{"slot": "x"}]}


def test_skips_unparsable_braces_before_the_object():
    assert parse_json_object('note {not json} then {"ok": true}') == {"ok": True}


def test_no_object_returns_none():
    assert parse_json_object("no json here") is None
    assert parse_json_object("") is None
    assert parse_json_object("[1, 2]") is None


def test_hebrew_acronym_quote_is_repaired_and_counted():
    reply = '{"updates": [{"slot": "x", "value": "y", "quote": "איך הרשב"ם מסביר"}]}'
    import json

    try:
        json.loads(reply)
        raise AssertionError("the unrepaired reply must not be valid JSON")
    except json.JSONDecodeError:
        pass
    diagnostics = Counter()
    parsed = parse_json_object(reply, diagnostics)
    assert parsed["updates"][0]["quote"] == 'איך הרשב"ם מסביר'
    assert diagnostics["reply_repaired"] == 1


def test_valid_json_is_not_counted_as_repaired_and_other_failures_stay_none():
    diagnostics = Counter()
    assert parse_json_object('{"quote": "רשב\\"ם"}', diagnostics) is not None
    assert parse_json_object('{"quote": "unterminated', diagnostics) is None
    assert diagnostics["reply_repaired"] == 0


def test_normalize_quote_maps_hebrew_and_curly_quotes_and_whitespace():
    assert normalize_quote("רשב״ם   \n ו׳ \u201cטוב\u201d \u2019") == 'רשב"ם ו\' "טוב" \''
    assert normalize_quote("e\u0301") == normalize_quote("\u00e9")


def test_locate_quote_returns_the_learners_own_substring():
    text = "איך הרשב״ם   מסביר  את\nהפסוק?"
    assert locate_quote('הרשב"ם מסביר את הפסוק', text) == "הרשב״ם   מסביר  את\nהפסוק"
    assert locate_quote("הרשב״ם", text) == "הרשב״ם"
    assert locate_quote("It\u2019s fine", "He said it's fine, ok") is None  # case differs
    assert locate_quote("it\u2019s fine", "He said it's fine, ok") == "it's fine"
    assert locate_quote("  ", text) is None and locate_quote("", text) is None
    assert locate_quote("not there", text) is None
    assert locate_quote("caf\u00e9", "a cafe\u0301 here") == "cafe\u0301".replace(
        "e\u0301", "\u00e9"
    )
