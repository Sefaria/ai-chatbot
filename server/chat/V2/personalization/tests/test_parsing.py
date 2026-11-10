from chat.V2.personalization.parsing import parse_json_object


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
