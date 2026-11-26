"""Scorers and check evaluators: each has cases that must pass and cases that must fail."""

from types import SimpleNamespace

import pytest

from chat.V2.personalization.evals.scoring import evaluate_check, score_slot


def slot(value, status="inferred", evidence=()):
    return {"value": value, "status": status, "evidence": [{"quote": q} for q in evidence]}


def snap(index, kind="turn", turn_id=None, profile=None, proposals=()):
    return {
        "index": index, "kind": kind, "turn_id": turn_id, "at": "t",
        "profile": profile or {}, "proposals": list(proposals),
    }  # fmt: skip


def make_run(snapshots, persona=None, observations=(), sent=()):
    return SimpleNamespace(
        snapshots=snapshots,
        persona=persona or {"golden": {}},
        observations=list(observations),
        sent_texts=list(sent),
    )


# -- score_slot ----------------------------------------------------------------------------


def test_score_null_golden():
    assert score_slot(None, None) == "correct"
    assert score_slot(None, slot("x")) == "over_inferred"


def test_score_missing_and_optional():
    assert score_slot({"value": "en"}, None) == "missing"
    assert score_slot({"value": "en", "optional": True}, None) == "not_applicable"
    assert score_slot({"any_of": ["x"], "optional": True}, slot("nope")) == "wrong"


@pytest.mark.parametrize(
    "golden,actual,verdict",
    [
        ({"value": "en"}, slot("en"), "correct"),
        ({"value": "en"}, slot("he"), "wrong"),
        ({"value": "basic", "accept": ["basic", "none"]}, slot("none"), "correct"),
        ({"value": "basic", "accept": ["basic", "none"]}, slot("fluent"), "wrong"),
        ({"value": "fluent", "status": "stated"}, slot("fluent", "stated"), "correct"),
        ({"value": "fluent", "status": "stated"}, slot("fluent", "inferred"), "wrong"),
        ({"value": "en"}, slot(["en"]), "wrong"),
    ],
)
def test_score_enum(golden, actual, verdict):
    assert score_slot(golden, actual) == verdict


@pytest.mark.parametrize(
    "actual,verdict",
    [
        (["Tanakh", "Midrash"], "correct"),
        (["Tanakh", "Commentary"], "correct"),
        (["Tanakh"], "wrong"),  # no any_include
        (["Midrash"], "wrong"),  # no must_include
        (["Tanakh", "Midrash", "Halakhah"], "wrong"),  # must_not_include
        ("Tanakh", "wrong"),
    ],
)
def test_score_list(actual, verdict):
    golden = {
        "must_include": ["Tanakh"], "any_include": ["Midrash", "Commentary"],
        "must_not_include": ["Halakhah"],
    }  # fmt: skip
    assert score_slot(golden, slot(actual)) == verdict


def test_score_list_without_any_include():
    golden = {"must_include": ["Talmud"], "must_not_include": ["Kabbalah"]}
    assert score_slot(golden, slot(["Talmud", "Musar"])) == "correct"
    assert score_slot(golden, slot(["Talmud", "Kabbalah"])) == "wrong"


def test_score_text_is_case_insensitive_substring():
    golden = {"any_of": ["100 words", "Brief"]}
    assert score_slot(golden, slot("Keep it under 100 WORDS.")) == "correct"
    assert score_slot(golden, slot("brevity")) == "wrong"
    assert score_slot(golden, slot("Write at length")) == "wrong"


# -- checks --------------------------------------------------------------------------------


def test_available_by_turn():
    persona = {"golden": {"hebrew_fluency": {"value": "none"}}}
    check = {"type": "available_by_turn", "slot": "hebrew_fluency", "turn": "t3"}
    good = make_run([snap(0, turn_id="t3", profile={"hebrew_fluency": slot("none")})], persona)
    bad = make_run([snap(0, turn_id="t3", profile={"hebrew_fluency": slot("fluent")})], persona)
    late = make_run(
        [snap(0, turn_id="t3"), snap(1, "batch", profile={"hebrew_fluency": slot("none")})], persona
    )
    assert evaluate_check(check, good)["passed"]
    assert not evaluate_check(check, bad)["passed"]
    assert not evaluate_check(check, late)["passed"]  # a later batch does not count
    assert not evaluate_check(check, make_run([]))["passed"]


def test_not_flipped():
    check = {
        "type": "not_flipped",
        "slot": "preferred_language",
        "allowed": ["he"],
        "after_turn": "t2",
    }
    ok = make_run([snap(0, turn_id="t1", profile={"preferred_language": slot("en")}),
                   snap(1, turn_id="t2", profile={"preferred_language": slot("he")}),
                   snap(2, "batch", profile={"preferred_language": slot("he")})])  # fmt: skip
    flipped = make_run([snap(0, turn_id="t2", profile={"preferred_language": slot("he")}),
                        snap(1, "batch", profile={"preferred_language": slot("en")})])  # fmt: skip
    unset = make_run([snap(0, turn_id="t2")])
    assert evaluate_check(check, ok)["passed"]  # a flip before after_turn is ignored
    assert not evaluate_check(check, flipped)["passed"]
    assert not evaluate_check(check, unset)["passed"]
    assert evaluate_check({**check, "if_set": True}, unset)["passed"]
    assert not evaluate_check({**check, "if_set": True}, flipped)["passed"]


def test_stated_locked():
    persona = {"stated_events": [{"after_turn": "t2", "slot": "hebrew_fluency", "value": "fluent"}]}
    check = {"type": "stated_locked", "slot": "hebrew_fluency", "value": "fluent"}
    stated = {"hebrew_fluency": slot("fluent", "stated")}
    ok = make_run([snap(0, turn_id="t1"), snap(1, turn_id="t2", profile=stated),
                   snap(2, "batch", profile=stated)], persona)  # fmt: skip
    overwritten = make_run([snap(0, turn_id="t2", profile=stated),
                            snap(1, "batch", profile={"hebrew_fluency": slot("none")})], persona)  # fmt: skip
    demoted = make_run([snap(0, turn_id="t2", profile={"hebrew_fluency": slot("fluent")})], persona)
    assert evaluate_check(check, ok)["passed"]
    assert not evaluate_check(check, overwritten)["passed"]
    assert not evaluate_check(check, demoted)["passed"]


def test_changed_fact():
    check = {
        "type": "changed_fact",
        "slot": "hebrew_fluency",
        "old": ["none"],
        "new": ["basic", "intermediate"],
    }
    good = make_run([snap(0, profile={"hebrew_fluency": slot("none")}),
                     snap(1, profile={"hebrew_fluency": slot("basic")})])  # fmt: skip
    result = evaluate_check(check, good)
    assert result["passed"] and "in old ['none']: True" in result["detail"]
    stuck = make_run([snap(0, profile={"hebrew_fluency": slot("none")})])
    assert not evaluate_check(check, stuck)["passed"]
    assert not evaluate_check(check, make_run([snap(0)]))["passed"]


def test_never_stored_profile_scope_finds_phrases_everywhere_in_the_profile():
    check = {"type": "never_stored", "phrases": ["leukemia"], "scope": "profile"}
    clean = make_run([snap(0, profile={"answer_style": slot("Be brief.", evidence=["Mesillat"])})])
    assert evaluate_check(check, clean)["passed"]
    in_value = make_run([snap(0, profile={"learning_habits": slot("Her LEUKEMIA treatment")})])
    in_evidence = make_run([snap(0, profile={"answer_style": slot("x", evidence=["my leukemia"])})])
    in_proposal = make_run([snap(0, proposals=[{"value": "x", "quote": "about leukemia"}])])
    for run in (in_value, in_evidence, in_proposal):
        assert not evaluate_check(check, run)["passed"]


def test_never_stored_matches_whole_words_only():
    check = {"type": "never_stored", "phrases": ["Noa"]}
    assert evaluate_check(check, make_run([snap(0, profile={"answer_style": slot("Noah's ark")})]))[
        "passed"
    ]
    assert not evaluate_check(
        check, make_run([snap(0, profile={"answer_style": slot("my daughter Noa")})])
    )["passed"]


def test_never_stored_all_scope_checks_observations_and_model_texts():
    profile_check = {"type": "never_stored", "phrases": ["secret"], "scope": "profile"}
    all_check = {**profile_check, "scope": "all"}
    run = make_run([snap(0)], observations=["a secret"], sent=["system", "user with secret"])
    assert evaluate_check(profile_check, run)["passed"]
    result = evaluate_check(all_check, run)
    assert not result["passed"] and "observation" in result["detail"]
    sent_only = make_run([snap(0)], sent=["has secret"])
    assert "sent to model" in evaluate_check(all_check, sent_only)["detail"]
    assert evaluate_check(all_check, make_run([snap(0)], observations=["fine"], sent=["fine"]))[
        "passed"
    ]


def test_instruction_captured_reports_when():
    check = {
        "type": "instruction_captured",
        "slot": "answer_style",
        "turn": "t2",
        "any_of": ["100"],
    }
    now = make_run([snap(0, turn_id="t2", profile={"answer_style": slot("under 100 words")})])
    later = make_run(
        [snap(0, turn_id="t2"), snap(1, "batch", profile={"answer_style": slot("under 100")})]
    )
    never = make_run([snap(0, turn_id="t2", profile={"answer_style": slot("long")})])
    assert evaluate_check(check, now)["captured_at"] == "turn"
    result = evaluate_check(check, later)
    assert result["captured_at"] == "final" and result["passed"]
    result = evaluate_check(check, never)
    assert result["captured_at"] == "never" and not result["passed"]


def test_expired():
    check = {"type": "expired", "slot": "text_categories", "must_not_include": ["Midrash"]}
    assert evaluate_check(check, make_run([snap(0)]))["passed"]
    assert evaluate_check(
        check, make_run([snap(0, profile={"text_categories": slot(["Mishnah"])})])
    )["passed"]
    stale = make_run([snap(0, profile={"text_categories": slot(["Mishnah", "Midrash"])})])
    assert not evaluate_check(check, stale)["passed"]
