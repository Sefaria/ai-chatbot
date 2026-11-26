"""The profile-update input builder and output validator. Made-up text only."""

import json
from datetime import timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from chat.models import ChatSession
from chat.V2.personalization import profile_store as store
from chat.V2.personalization.core_slots import CORE_HEADINGS, CORE_KEYS
from chat.V2.personalization.update_io import (
    UpdateOutputError,
    build_update_input,
    support_dates,
    validate_update_output,
)

from .test_profile_store import NOW, add_prompt, section

pytestmark = pytest.mark.django_db

U = "upd-user"


def day(n):
    return NOW - timedelta(days=n)


def fill(count, *, user=U, start_days=1):
    for i in range(count):
        add_prompt(user, f"question number {i}", at=day(start_days) + timedelta(minutes=i))


def test_blocks_and_order():
    store.replace_sections(
        U,
        0,
        [
            section("Reply language", "You want Hebrew.", key="reply_language", origin="stated"),
            section("Teaching", "You teach a class.", kind="dynamic", origin="user", position=1),
        ],
        updated_by="job",
        processed_through=None,
        set_notice=False,
    )
    store.set_instructions(U, "Keep it short.")
    add_prompt(U, "what is a sugya", at=day(2))
    text, prompts = build_update_input(U, NOW)
    order = [
        text.index(t)
        for t in (
            "<today>2026-11-12</today>",
            "<current_profile>",
            "<instructions>",
            "<allowed_core_slots>",
            "<language_signal>en</language_signal>",
            "<prompts>",
        )
    ]
    assert order == sorted(order)
    assert (
        "[reply_language] Reply language (stated, last supported 2026-11-12)\nYou want Hebrew."
        in text
    )
    assert "[dynamic] Teaching (user, last supported 2026-11-12)" in text
    assert ", ".join(CORE_KEYS) in text
    assert "Keep it short." in text
    assert "[P1] 2026-11-10 what is a sugya" in text
    assert len(prompts) == 1


def test_empty_profile_says_none():
    text, prompts = build_update_input(U, NOW)
    assert "<current_profile>\nnone\n</current_profile>" in text
    assert prompts == []


def test_instructions_omitted_when_off_or_empty():
    store.set_instructions(U, "Keep it short.")
    store.set_enabled(U, "instructions", False)
    assert "<instructions>" not in build_update_input(U, NOW)[0]
    store.set_enabled(U, "instructions", True)
    store.set_instructions(U, "   ")
    assert "<instructions>" not in build_update_input(U, NOW)[0]


def test_cap_keeps_newest_150_oldest_first():
    fill(155)
    text, prompts = build_update_input(U, NOW)
    assert len(prompts) == 150
    assert prompts[0].text == "question number 5"
    assert prompts[-1].text == "question number 154"
    assert "[P1] 2026-11-11 question number 5\n" in text
    assert "[P150] 2026-11-11 question number 154\n" in text


def test_prompts_of_any_age_and_no_minimum():
    add_prompt(U, "an old question", at=day(400))
    _, prompts = build_update_input(U, NOW)
    assert [p.text for p in prompts] == ["an old question"]


def test_cleared_at_floor():
    add_prompt(U, "before the clear", at=day(10))
    store.clear_profile(U)
    add_prompt(U, "after the clear")
    _, prompts = build_update_input(U, NOW + timedelta(days=1))
    assert [p.text for p in prompts] == ["after the clear"]


def test_duplicates_removed_keeping_latest_date():
    add_prompt(U, "same words", at=day(5))
    add_prompt(U, "other words", at=day(4))
    add_prompt(U, "same words", at=day(3))
    text, prompts = build_update_input(U, NOW)
    assert [p.text for p in prompts] == ["other words", "same words"]
    assert "[P2] 2026-11-09 same words" in text
    assert "S1" not in text


def test_screening_still_applies():
    add_prompt(U, "a fine question", at=day(3))
    add_prompt(U, "hidden one", at=day(2), session="hid")
    ChatSession.objects.create(session_id="hid", user_id=U, is_deleted=True)
    _, prompts = build_update_input(U, NOW)
    assert [p.text for p in prompts] == ["a fine question"]


def test_support_dates_newest():
    add_prompt(U, "first", at=day(5))
    add_prompt(U, "second", at=day(2))
    _, prompts = build_update_input(U, NOW)
    assert support_dates({"support": [1, 2]}, prompts) == day(2)
    assert support_dates({"support": [1]}, prompts) == day(5)


def out(*sections):
    return json.dumps({"sections": list(sections)})


def sec(key="reply_language", body="You read in Hebrew.", origin="stated", support=(1,), **kw):
    return {
        "key": key,
        "heading": kw.pop("heading", "x"),
        "body": body,
        "origin": origin,
        "support": list(support),
        **kw,
    }


def dyn(heading="Teaching", **kw):
    return sec(key=None, heading=heading, **kw)


def test_validator_accepts_empty_and_good():
    assert validate_update_output('{"sections": []}', 0) == []
    good = validate_update_output(
        "```json\n" + out(sec(), dyn(), sec("answer_style", origin="user", support=())) + "\n```", 3
    )
    assert [s["key"] for s in good] == ["reply_language", None, "answer_style"]
    assert good[0]["heading"] == CORE_HEADINGS["reply_language"]


@pytest.mark.parametrize(
    "raw,n,reason",
    [
        ("not json", 3, "not_json_sections"),
        ('{"sections": "x"}', 3, "not_json_sections"),
        (out("str"), 3, "bad_section"),
        (out(sec("made_up")), 3, "bad_key"),
        (out(sec(body="")), 3, "bad_body"),
        (out(dyn(heading="")), 3, "bad_heading"),
        (out(sec(origin="guess")), 3, "bad_origin"),
        (out(sec(support=[4])), 3, "bad_support"),
        (out(sec(support=[0])), 3, "bad_support"),
        (out(sec(support=[True])), 3, "bad_support"),
        (out(sec(support=[1.5])), 3, "bad_support"),
        (out(sec(support="1")), 3, "bad_support"),
        (out(sec(support=[])), 3, "missing_support"),
        (out(sec(origin="inferred", support=[])), 3, "missing_support"),
        (out(sec(), sec()), 3, "duplicate_key"),
        (out(dyn("Teaching"), dyn(" teaching ")), 3, "duplicate_heading"),
        (out(sec(), dyn(CORE_HEADINGS["reply_language"])), 3, "duplicate_heading"),
        (out(*[dyn(f"Topic {i}") for i in range(5)]), 3, "too_many_dynamic"),
        (out(sec(body="word " * 150)), 3, "too_long"),
        (out(sec(body="word " * 80), dyn(body="word " * 70)), 3, "too_long"),
    ],
)
def test_validator_rejects(raw, n, reason):
    with pytest.raises(UpdateOutputError) as e:
        validate_update_output(raw, n)
    assert e.value.reason == reason


def test_validator_boundaries():
    assert validate_update_output(out(sec(body="word " * 149)), 1)
    assert len(validate_update_output(out(*[dyn(f"Topic {i}") for i in range(4)]), 1)) == 4


_scalar = st.one_of(
    st.none(), st.booleans(), st.integers(-3, 8), st.floats(allow_nan=False), st.text(max_size=12)
)
_key = st.one_of(st.none(), st.sampled_from(CORE_KEYS), st.text(max_size=6))
_entry = st.fixed_dictionaries(
    {},
    optional={
        "key": _key,
        "heading": st.text(max_size=12),
        "body": st.one_of(st.text(max_size=40), _scalar),
        "origin": st.one_of(st.sampled_from(["stated", "inferred", "user"]), _scalar),
        "support": st.one_of(st.lists(st.one_of(st.integers(-1, 6), _scalar), max_size=4), _scalar),
    },
)
_json = st.one_of(
    st.text(max_size=30),
    st.recursive(
        _scalar,
        lambda c: st.lists(c, max_size=3) | st.dictionaries(st.text(max_size=4), c, max_size=3),
        max_leaves=6,
    ).map(json.dumps),
    st.dictionaries(st.just("sections"), st.lists(st.one_of(_entry, _scalar), max_size=7)).map(
        json.dumps
    ),
)


@settings(max_examples=400, deadline=None)
@given(raw=_json, n=st.integers(0, 6))
def test_validator_only_raises_its_own_error_and_accepted_output_obeys_every_rule(raw, n):
    try:
        result = validate_update_output(raw, n)
    except UpdateOutputError:
        return
    keys = [s["key"] for s in result if s["key"] is not None]
    headings = [" ".join(s["heading"].lower().split()) for s in result]
    assert all(k in CORE_KEYS for k in keys)
    assert len(keys) == len(set(keys))
    assert len(headings) == len(set(headings))
    assert sum(1 for s in result if s["key"] is None) <= 4
    assert sum(len(s["body"].split()) for s in result) < 150
    for s in result:
        assert s["origin"] in ("stated", "inferred", "user")
        assert all(type(x) is int and 1 <= x <= n for x in s["support"])
        if s["origin"] != "user":
            assert s["support"]
        assert s["body"] and s["heading"]
