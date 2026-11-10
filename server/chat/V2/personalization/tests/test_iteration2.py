"""Pipeline fixes from the first live run: expiry order, excluded instructions, language,
diagnostics and tolerant quote matching."""

import json
from collections import Counter

from chat.V2.personalization.evals import run_memory_eval
from chat.V2.personalization.jev import FakeJevClient, JevAnswer, JevError
from chat.V2.personalization.strategies.prompts import CONSOLIDATION_SYSTEM, REALTIME_SYSTEM

from .conftest import NOW, day, make_strategy, make_turn
from .test_hybrid_jev import choice, jev_with
from .test_nightly import consolidation, log_turns, update

EXCLUDE = "From now on, never show me anything written by Reform rabbis."
CARRY_OVER = "do not carry over categories from the current profile"


def stale_profile(store):
    store.set_inferred("u1", "text_categories", ["Midrash"], [], day(-70), expires_at=day(-10))


# -- 1. expiry before the profile is read ---------------------------------------------------


def test_prompts_tell_the_model_not_to_carry_over_categories():
    assert CARRY_OVER in REALTIME_SYSTEM and CARRY_OVER in CONSOLIDATION_SYSTEM


def test_nightly_batch_expires_before_asking_the_model(store, registry):
    stale_profile(store)
    strategy, llm, _ = make_strategy("nightly", store, registry)
    log_turns(strategy, [("s1", "m1", "Explain Berakhot", day(-1))])
    strategy.run_batch(NOW)
    assert "Midrash" not in llm.calls[0]["user"]


def test_realtime_expires_before_asking_the_model(store, registry):
    stale_profile(store)
    strategy, llm, _ = make_strategy("realtime", store, registry)
    strategy.on_turn(make_turn("Explain Berakhot"), NOW)
    assert "Midrash" not in llm.calls[0]["user"]


def test_expired_category_is_not_merged_back_with_a_fresh_expiry(store, registry):
    stale_profile(store)
    reassert = update("text_categories", ["Midrash"], ("m1", "Explain Berakhot"))
    strategy, _, _ = make_strategy("nightly", store, registry, consolidation(reassert))
    log_turns(strategy, [("s1", "m1", "Explain Berakhot", day(-1))])
    strategy.run_batch(NOW)
    slot = store.get_profile("u1")["text_categories"]
    # the model asserts it from the prompts shown, so it is a fresh value with a fresh expiry
    assert slot.value == ["Midrash"] and slot.expires_at == day(60)
    store.clear("u1")
    stale_profile(store)
    strategy, _, _ = make_strategy("nightly", store, registry)
    log_turns(strategy, [("s1", "m1", "Explain Berakhot", day(-1))])
    strategy.run_batch(NOW)
    assert "text_categories" not in store.get_profile("u1")


def test_hybrid_jev_expires_before_judging(store, registry):
    stale_profile(store)
    strategy, _, _ = make_strategy("hybrid_jev", store, registry, jev=jev_with())
    strategy.on_turn(make_turn("Explain Berakhot"), NOW)
    assert "text_categories" not in store.get_profile("u1")


def test_render_ignores_an_expired_slot_even_if_it_was_not_swept(store, registry):
    from chat.V2.personalization.render import render_profile

    stale_profile(store)
    assert render_profile(store.get_profile("u1"), registry, NOW) == ("", [])


# -- 2. excluded instructions and Jev evidence ----------------------------------------------


def test_hybrid_jev_exclude_instruction_skips_inference_and_marks_the_observation(store, registry):
    jev = jev_with(
        {"hebrew_fluency": choice("fluent", 1.0), "category:Tanakh": JevAnswer("noul", noul=0.9)},
        instruction=(0.9, 0.97),
    )
    strategy, _, _ = make_strategy("hybrid_jev", store, registry, jev=jev)
    result = strategy.on_turn(make_turn(EXCLUDE), NOW)
    assert result.skipped_reason == "instruction_excludes_sources" and result.proposals == []
    assert len(jev.calls) == 1 and "message" in jev.calls[0][0]  # no slot inference
    assert store.get_profile("u1") == {}
    (obs,) = store.observations("u1", include_quarantined=True)
    assert obs.quarantined and obs.text == ""


def test_excluded_prompt_never_reaches_a_window_or_an_evidence_line(store, registry):
    jev = jev_with({"category:Tanakh": JevAnswer("noul", noul=0.9)}, instruction=(0.0, 0.97))
    strategy, llm, _ = make_strategy("hybrid_jev", store, registry, jev=jev)
    strategy.on_turn(make_turn(EXCLUDE, message="m1", at=day(-2)), day(-2))
    strategy.on_turn(make_turn("Tell me about Genesis", message="m2", at=day(-1)), day(-1))
    window = jev.calls[-1][0]["learner_prompts"]
    assert [p["text"] for p in window] == ["Tell me about Genesis"]
    profile = store.get_profile("u1")
    assert "Reform" not in json.dumps(
        [[e.quote for e in slot.evidence] for slot in profile.values()]
    )
    strategy.run_batch(NOW)  # the consolidation input leaves it out too
    assert all("Reform" not in call["user"] for call in llm.calls)


def test_jev_evidence_skips_prompts_with_a_standing_instruction(store, registry):
    answers = {"category:Talmud": JevAnswer("noul", noul=0.9)}
    jev = jev_with(answers, instruction=(0.9, 0.0))
    strategy, _, _ = make_strategy("hybrid_jev", store, registry, jev=jev)
    strategy.on_turn(make_turn("Explain Berakhot 2a", message="m1", at=day(-2)), day(-2))
    strategy.on_turn(make_turn("Always give me sources first", message="m2", at=day(-1)), day(-1))
    (quote,) = [e.quote for e in store.get_profile("u1")["text_categories"].evidence]
    assert quote == "Explain Berakhot 2a"
    # when every prompt holds an instruction the window is empty (each is a content-free
    # marker): no closed-set request is made and nothing is written
    store.clear("u1")
    only_instructions = jev_with(answers, (0.9, 0.0))
    strategy, _, _ = make_strategy("hybrid_jev", store, registry, jev=only_instructions)
    strategy.on_turn(make_turn("Always give me sources first", at=day(-1)), day(-1))
    assert "text_categories" not in store.get_profile("u1")
    assert all("message" in state for state, _ in only_instructions.calls)


def test_hybrid_mapper_flags_excluded_instructions(store, registry):
    reply = lambda s, u: '{"slot": null, "excludes_sources": true}'  # noqa: E731
    strategy, llm, _ = make_strategy("hybrid", store, registry, reply)
    result = strategy.on_turn(make_turn(EXCLUDE, message="m1", at=day(-1)), day(-1))
    assert result.skipped_reason == "instruction_excludes_sources" and result.llm_calls == 1
    assert "excludes_sources" in llm.calls[0]["system"]
    assert store.get_profile("u1") == {} and store.proposals("u1") == []
    (obs,) = store.observations("u1", include_quarantined=True)
    assert obs.quarantined and obs.text == ""
    strategy.on_turn(make_turn("Explain Berakhot", message="m2", at=day(-1)), day(-1))
    strategy.run_batch(NOW)
    assert all("Reform" not in call["user"] for call in llm.calls[1:])


# -- 3. the language rule inside the strategies ----------------------------------------------

PASTE = "I found this but cannot read it: שלא לאכול בשר בחלב שנאמר לא תבשל גדי בחלב אמו"


def test_a_pasted_passage_does_not_flip_the_language(store, registry):
    strategy, _, _ = make_strategy("hybrid", store, registry)
    strategy.on_turn(
        make_turn("What is the source?", session="s1", message="m1", at=day(-3)), day(-3)
    )
    strategy.on_turn(make_turn(PASTE, session="s2", message="m2", at=day(-2)), day(-2))
    strategy.on_turn(make_turn("Thank you", session="s3", message="m3", at=day(-1)), day(-1))
    assert store.get_profile("u1")["preferred_language"].value == "en"


def test_realtime_ignores_a_mixed_prompt(store, registry):
    strategy, _, _ = make_strategy("realtime", store, registry)
    strategy.on_turn(make_turn("What is the source?", message="m1"), NOW)
    strategy.on_turn(make_turn(PASTE, message="m2"), NOW)
    assert store.get_profile("u1")["preferred_language"].value == "en"


def test_one_english_prompt_in_a_hebrew_history_does_not_flip(store, registry):
    strategy, _, _ = make_strategy("nightly", store, registry)
    log_turns(strategy, [("s1", "m1", "מה זה סוגיה", day(-3)), ("s2", "m2", "מה זה גמרא", day(-2))])
    strategy.run_batch(day(-1))
    log_turns(strategy, [("s3", "m3", "explain kal vachomer simply", day(0))])
    strategy.run_batch(day(1))
    assert store.get_profile("u1")["preferred_language"].value == "he"


# -- 4. diagnostics ---------------------------------------------------------------------------

Q1, Q2 = "I read Hebrew slowly", "translate this Hebrew verse"


def test_diagnostics_applied_unchanged_and_invalid_value(store, registry):
    reply = json.dumps(
        {
            "updates": [
                {"slot": "hebrew_fluency", "value": "fluent", "quote": Q1},
                {"slot": "terminology_comfort", "value": "expert", "quote": Q1},
            ]
        }
    )
    strategy, _, _ = make_strategy("realtime", store, registry, lambda s, u: reply)
    first = strategy.on_turn(make_turn(Q1), NOW).diagnostics
    second = strategy.on_turn(make_turn(Q1, message="m2"), NOW).diagnostics
    d = Counter(first) + Counter(second)
    assert d["applied"] == 2  # the language and the fluency
    assert d["value_invalid"] == 2 and d["unchanged"] >= 2


def test_diagnostics_unparsable_reply_and_fabricated_quote(store, registry):
    strategy, _, _ = make_strategy("realtime", store, registry, lambda s, u: "no json at all")
    assert strategy.on_turn(make_turn(Q1), NOW).diagnostics["reply_unparsable"] == 1
    fabricated = json.dumps(
        {"updates": [{"slot": "hebrew_fluency", "value": "basic", "quote": "x y"}]}
    )
    strategy, _, _ = make_strategy("realtime", store, registry, lambda s, u: fabricated)
    d = strategy.on_turn(make_turn(Q1, message="m2"), NOW).diagnostics
    assert d["quote_not_verbatim"] == 1
    assert d["no_valid_evidence"] == 1


def test_diagnostics_stated_wins(store, registry):
    store.set_stated("u1", "hebrew_fluency", "none", day(-1))
    reply = json.dumps({"updates": [{"slot": "hebrew_fluency", "value": "fluent", "quote": Q1}]})
    strategy, _, _ = make_strategy("realtime", store, registry, lambda s, u: reply)
    assert strategy.on_turn(make_turn(Q1), NOW).diagnostics["stated_wins"] == 1


def test_diagnostics_below_min_sessions_and_missing_evidence(store, registry):
    one_session = update("hebrew_fluency", "basic", ("m1", Q1), ("m2", Q2))
    no_evidence = update("terminology_comfort", "some", ("m1", "never said"))
    strategy, _, _ = make_strategy(
        "nightly", store, registry, consolidation(one_session, no_evidence)
    )
    log_turns(strategy, [("s1", "m1", Q1, day(-2)), ("s1", "m2", Q2, day(-1))])
    d = strategy.run_batch(NOW).diagnostics
    assert d["below_min_sessions"] == 1
    assert d["quote_not_verbatim"] == 1
    assert d["no_valid_evidence"] == 1


def test_diagnostics_jev_reasons(store, registry):
    store.set_inferred("u1", "hebrew_fluency", "none", [], day(-5))
    jev = jev_with(
        {
            "hebrew_fluency": choice("fluent", 0.7),  # below the change threshold
            "terminology_comfort": choice("unclear", 1.0),  # uncertain
            "register": choice("traditional", 0.9),  # set, nothing to report
            "category:Tanakh": JevAnswer("noul", noul=0.5),  # uncertain band
        }
    )
    strategy, _, _ = make_strategy("hybrid_jev", store, registry, jev=jev)
    d = strategy.on_turn(make_turn("hello"), NOW).diagnostics
    assert d["jev_below_change_threshold"] == 1 and d["jev_uncertain"] == 2

    def boom(state, questions):
        raise JevError("Jev HTTP 500")

    strategy, _, _ = make_strategy("hybrid_jev", store, registry, jev=FakeJevClient(boom))
    assert strategy.on_turn(make_turn("hello", message="m2"), NOW).diagnostics["jev_error"] == 1


def test_a_hebrew_acronym_reply_is_repaired_and_applied(store, registry):
    text = "איך הרשב״ם מסביר את הפסוק, אני קורא עברית"
    reply = (
        '{"updates": [{"slot": "hebrew_fluency", "value": "fluent", "quote": "איך הרשב"ם מסביר"}]}'
    )
    strategy, _, _ = make_strategy("realtime", store, registry, lambda s, u: reply)
    d = strategy.on_turn(make_turn(text), NOW).diagnostics
    assert d["reply_repaired"] == 1 and d.get("reply_unparsable", 0) == 0
    slot = store.get_profile("u1")["hebrew_fluency"]
    assert slot.value == "fluent" and slot.evidence[0].quote == "איך הרשב״ם מסביר"  # learner's own


def test_quote_check_tolerates_quote_marks_whitespace_and_unicode_form(store, registry):
    text = "She said “read it”   slowly, café"
    reply = json.dumps(
        {
            "updates": [
                {"slot": "answer_style", "value": "Go slowly.", "quote": 'said "read it" slowly,'}
            ]
        }
    )
    strategy, _, _ = make_strategy("realtime", store, registry, lambda s, u: reply)
    d = strategy.on_turn(make_turn(text), NOW).diagnostics
    assert store.get_profile("u1")["answer_style"].evidence[0].quote == "said “read it”   slowly,"
    assert d.get("quote_not_verbatim", 0) == 0


# -- 5. harness ----------------------------------------------------------------------------


def test_results_carry_diagnostics_and_the_why_table(tmp_path):
    out = tmp_path / "d"
    assert (
        run_memory_eval.main(
            ["--llm", "oracle", "--out", str(out), "--personas", "tamar-instruction,noam-stale"]
        )
        == 0
    )
    data = json.loads((out / "memory_results.json").read_text())
    realtime = data["strategies"]["realtime"]
    assert realtime["diagnostics"]["applied"] > 0
    assert set(realtime["personas"]["noam-stale"]["diagnostics"]) <= set(
        run_memory_eval.DIAGNOSTIC_KEYS
    )
    assert "Why updates were dropped" in (out / "memory_results.md").read_text()


def test_runs_argument_writes_per_run_results_and_a_summary(tmp_path):
    out = tmp_path / "r"
    args = ["--llm", "oracle", "--runs", "2", "--out", str(out), "--personas", "rachel-anon"]
    assert run_memory_eval.main(args) == 0
    data = json.loads((out / "memory_results.json").read_text())
    assert data["meta"]["runs"] == 2 and len(data["runs"]) == 2
    assert (out / "memory_results_run1.json").exists() and (
        out / "memory_results_run2.json"
    ).exists()
    stat = data["summary"]["realtime"]["slot_accuracy"]
    assert set(stat) == {"mean", "min", "max"} and stat["min"] <= stat["mean"] <= stat["max"]
    assert list(data["summary"]) == list(data["strategies"])


def test_aggregate_shows_mean_and_range():
    def fake_run(accuracy, passed):
        return {
            "strategies": {
                "s": {
                    "slot_accuracy": {"accuracy": accuracy, "per_slot": {"a": {"accuracy": accuracy}}},
                    "checks": {"t": {"passed": passed, "total": 4}},
                    "in_session": [{"passed": True}],
                    "cost": dict.fromkeys(("calls_per_turn", "jev_requests_per_turn", "jev_input_tokens_per_turn", "jev_avg_latency_ms", "tokens_per_turn"), 1.0)
                    | {"usd_per_1000_turns": {"claude-haiku-4-5": 1.0, "claude-haiku-5-5": 0.1}},
                    "diagnostics": {"applied": 2},
                }
            }
        }  # fmt: skip

    summary = run_memory_eval.aggregate([fake_run(0.5, 2), fake_run(0.9, 4)])
    s = summary["s"]
    assert s["slot_accuracy"] == {"mean": 0.7, "min": 0.5, "max": 0.9}
    assert s["checks_passed"] == {"mean": 3.0, "min": 2, "max": 4}
    assert s["diagnostics"]["applied"] == 4
    table = run_memory_eval.summary_table({"summary": summary})
    assert "70% (50%-90%)" in table and "3 (2-4)/4" in table
