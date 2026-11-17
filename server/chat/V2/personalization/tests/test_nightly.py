import json

from chat.V2.personalization.types import Observation

from .conftest import NOW, day, make_strategy, make_turn

Q1, Q2, Q3 = (
    "what does this Hebrew word mean",
    "translate this Hebrew verse",
    "I read Hebrew slowly",
)


def clear_item(key, message="m1", quote=Q1):
    return {"slot": key, "evidence": [{"message_id": message, "quote": quote}]}


def consolidation(*updates, clear=()):
    return lambda system, user: json.dumps({"updates": list(updates), "clear": list(clear)})


def update(slot, value, *evidence):
    return {
        "slot": slot,
        "value": value,
        "evidence": [{"message_id": m, "quote": q} for m, q in evidence],
    }


def log_turns(strategy, turns):
    for session, message, text, at in turns:
        strategy.on_turn(make_turn(text, session=session, message=message, at=at), at)


def test_on_turn_logs_an_observation_without_a_model_call(store, registry):
    strategy, llm, _ = make_strategy("nightly", store, registry)
    result = strategy.on_turn(make_turn("Rashi on Genesis 1:1 מה זה"), NOW)
    assert result.llm_calls == 0 and llm.calls == [] and store.get_profile("u1") == {}
    (obs,) = store.observations("u1")
    assert obs.text.startswith("Rashi") and obs.session_id == "s1" and obs.message_id == "m1"
    assert obs.signals["language"] in {"en", "mixed"}
    assert obs.signals["cited_categories"] == ["Commentary", "Tanakh"]
    assert 0 < obs.signals["hebrew_ratio"] < 1


def test_batch_applies_update_with_enough_sessions_and_one_call_per_user(store, registry):
    upd = update("hebrew_fluency", "basic", ("m1", Q1), ("m2", Q2))
    strategy, llm, ledger = make_strategy("nightly", store, registry, consolidation(upd))
    log_turns(strategy, [("s1", "m1", Q1, day(-3)), ("s2", "m2", Q2, day(-1))])
    result = strategy.run_batch(NOW)
    slot = store.get_profile("u1")["hebrew_fluency"]
    assert (slot.value, slot.status, len(slot.evidence)) == ("basic", "inferred", 2)
    assert result.users_processed == 1 and result.llm_calls == 1 and len(llm.calls) == 1
    assert ("u1", "hebrew_fluency") in result.updated_slots
    assert store.users_with_pending() == []
    assert ledger.entries[0].phase == "batch"
    assert "Session s1 (" in llm.calls[0]["user"] and "[m2]" in llm.calls[0]["user"]


def test_min_sessions_is_enforced_by_code(store, registry):
    one_session = update("hebrew_fluency", "basic", ("m1", Q1), ("m2", Q2))
    strategy, _, _ = make_strategy("nightly", store, registry, consolidation(one_session))
    log_turns(strategy, [("s1", "m1", Q1, day(-2)), ("s1", "m2", Q2, day(-1))])
    strategy.run_batch(NOW)
    assert "hebrew_fluency" not in store.get_profile("u1")


def test_learning_habits_needs_three_sessions(store, registry):
    turns = [("s1", "m1", Q1, day(-3)), ("s2", "m2", Q2, day(-2)), ("s3", "m3", Q3, day(-1))]
    two = update("learning_habits", "Daf Yomi.", ("m1", Q1), ("m2", Q2))
    strategy, _, _ = make_strategy("nightly", store, registry, consolidation(two))
    log_turns(strategy, turns)
    strategy.run_batch(NOW)
    assert "learning_habits" not in store.get_profile("u1")
    three = update("learning_habits", "Daf Yomi.", ("m1", Q1), ("m2", Q2), ("m3", Q3))
    strategy, _, _ = make_strategy("nightly", store, registry, consolidation(three))
    # m1 to m3 are already logged (a retried message is not logged twice); a new turn makes the
    # user pending again and the window still holds all three.
    log_turns(strategy, [("s4", "m4", "another prompt", day(-1))])
    strategy.run_batch(day(1))
    assert store.get_profile("u1")["learning_habits"].value == "Daf Yomi."


def test_single_session_slot_gets_a_shelf_life(store, registry):
    upd = update("text_categories", ["Talmud", "Pizza"], ("m1", "Berakhot"))
    strategy, _, _ = make_strategy("nightly", store, registry, consolidation(upd))
    log_turns(strategy, [("s1", "m1", "Explain Berakhot 2a", day(-1))])
    strategy.run_batch(NOW)
    slot = store.get_profile("u1")["text_categories"]
    assert slot.value == ["Talmud"] and slot.expires_at == day(60)


def test_preferred_language_is_computed_in_code_over_sessions(store, registry):
    wrong = update("preferred_language", "he", ("m1", "hello"))
    strategy, _, _ = make_strategy("nightly", store, registry, consolidation(wrong))
    turns = [("s1", "m1", "hello there", day(-3)), ("s2", "m2", "מה זה סוגיה", day(-2)),
             ("s3", "m3", "מה זה גמרא", day(-1))]  # fmt: skip
    log_turns(strategy, turns)
    strategy.run_batch(NOW)
    # no current value: the first session vote wins, whatever came later (a known weak spot)
    assert store.get_profile("u1")["preferred_language"].value == "en"


def test_fabricated_quote_is_dropped_but_valid_evidence_keeps_the_update(store, registry):
    upd = update("hebrew_fluency", "basic", ("m1", Q1), ("m2", "never said this"), ("m9", Q2))
    strategy, _, _ = make_strategy("nightly", store, registry, consolidation(upd))
    log_turns(strategy, [("s1", "m1", Q1, day(-3)), ("s2", "m2", Q2, day(-1))])
    strategy.run_batch(NOW)
    assert "hebrew_fluency" not in store.get_profile("u1")  # only one valid session left
    store.clear("u1")
    ok = update("hebrew_fluency", "basic", ("m1", Q1), ("m2", "never said this"), ("m2", Q2))
    strategy, _, _ = make_strategy("nightly", store, registry, consolidation(ok))
    log_turns(strategy, [("s1", "m1", Q1, day(-3)), ("s2", "m2", Q2, day(-1))])
    strategy.run_batch(NOW)
    assert [e.quote for e in store.get_profile("u1")["hebrew_fluency"].evidence] == [Q1, Q2]


def test_invalid_enum_is_dropped(store, registry):
    upd = update("hebrew_fluency", "native", ("m1", Q1), ("m2", Q2))
    strategy, _, _ = make_strategy("nightly", store, registry, consolidation(upd))
    log_turns(strategy, [("s1", "m1", Q1, day(-3)), ("s2", "m2", Q2, day(-1))])
    strategy.run_batch(NOW)
    assert "hebrew_fluency" not in store.get_profile("u1")


def test_stated_slot_survives_the_batch_and_clear_only_touches_inferred(store, registry):
    store.set_stated("u1", "hebrew_fluency", "none", day(-9))
    store.set_inferred("u1", "answer_style", "Be long.", [], day(-9))
    upd = update("hebrew_fluency", "basic", ("m1", Q1), ("m2", Q2))
    responder = consolidation(
        upd,
        clear=[clear_item(k) for k in ("hebrew_fluency", "answer_style", "preferred_language")],
    )
    strategy, _, _ = make_strategy("nightly", store, registry, responder)
    log_turns(strategy, [("s1", "m1", Q1, day(-3)), ("s2", "m2", Q2, day(-1))])
    strategy.run_batch(NOW)
    profile = store.get_profile("u1")
    assert (profile["hebrew_fluency"].value, profile["hebrew_fluency"].status) == ("none", "stated")
    assert "answer_style" not in profile


def test_sensitive_turn_is_never_stored_or_sent(store, registry):
    strategy, llm, _ = make_strategy("nightly", store, registry)
    result = strategy.on_turn(make_turn("I was diagnosed with cancer"), NOW)
    assert result.skipped_reason == "sensitive" and store.observations("u1") == []
    store.add_observation(Observation("u1", "s0", "old", "sitting shiva for my father", day(-1)))
    store.add_observation(Observation("u1", "s1", "ok", "Explain Berakhot", day(-1)))
    strategy.run_batch(NOW)
    assert "shiva" not in llm.calls[0]["user"] and "Berakhot" in llm.calls[0]["user"]


def test_store_disabled_logs_nothing(store, registry):
    store.set_settings("u1", store_enabled=False)
    strategy, _, _ = make_strategy("nightly", store, registry)
    assert strategy.on_turn(make_turn("hi"), NOW).skipped_reason == "store_disabled"
    assert store.observations("u1") == []


def test_window_and_observation_cap(store, registry):
    strategy, llm, _ = make_strategy("nightly", store, registry)
    store.add_observation(Observation("u1", "old", "m_old", "ancient prompt", day(-45)))
    for i in range(50):
        store.add_observation(
            Observation(
                "u1", "s1", f"m{i}", f"prompt {i}", day(-20) + i * 1.0 * (day(1) - day(0)) / 100
            )
        )
    strategy.run_batch(NOW)
    sent = llm.calls[0]["user"]
    assert "ancient prompt" not in sent
    assert sent.count("] prompt ") == 40 and "[m49]" in sent and "[m9]" not in sent


def test_prompts_are_truncated_before_sending(store, registry):
    strategy, llm, _ = make_strategy("nightly", store, registry)
    log_turns(strategy, [("s1", "m1", "x" * 3000, day(-1))])
    strategy.run_batch(NOW)
    assert llm.calls[0]["user"].count("x") == 1000


def test_model_failure_leaves_observations_pending(store, registry):
    def boom(system, user):
        raise RuntimeError("model down")

    strategy, _, _ = make_strategy("nightly", store, registry, boom)
    log_turns(strategy, [("s1", "m1", Q1, day(-1))])
    result = strategy.run_batch(NOW)
    assert result.errors == 1 and result.users_processed == 0
    assert store.users_with_pending() == ["u1"]


def test_batch_for_given_users_and_unparsable_output(store, registry):
    strategy, llm, _ = make_strategy("nightly", store, registry, lambda s, u: "no json")
    log_turns(strategy, [("s1", "m1", Q1, day(-1))])
    strategy.on_turn(make_turn(Q2, user="u2"), day(-1))
    result = strategy.run_batch(NOW, user_ids=["u2"])
    assert result.users_processed == 0 and result.errors == 1 and len(llm.calls) == 1
    assert store.users_with_pending() == ["u1", "u2"]  # u2's observation stays pending


def test_batch_for_given_users_only_touches_those_users(store, registry):
    strategy, llm, _ = make_strategy("nightly", store, registry, consolidation())
    log_turns(strategy, [("s1", "m1", Q1, day(-1))])
    strategy.on_turn(make_turn(Q2, user="u2"), day(-1))
    result = strategy.run_batch(NOW, user_ids=["u2"])
    assert result.users_processed == 1 and len(llm.calls) == 1
    assert store.users_with_pending() == ["u1"]


def test_batch_expires_old_inferred_slots(store, registry):
    store.set_inferred("u9", "text_categories", ["Talmud"], [], day(-70), expires_at=day(-10))
    strategy, llm, _ = make_strategy("nightly", store, registry)
    strategy.run_batch(NOW)
    assert store.get_profile("u9") == {} and llm.calls == []
