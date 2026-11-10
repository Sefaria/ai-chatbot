import json

from .conftest import NOW, day, make_strategy, make_turn

FLUENT = "I read the Hebrew text fluently, no need to translate it"


def reply(*updates):
    return lambda system, user: json.dumps({"updates": list(updates)})


def fluency(quote=FLUENT, value="fluent"):
    return {"slot": "hebrew_fluency", "value": value, "quote": quote}


def test_applies_an_update_after_a_single_turn(store, registry):
    strategy, llm, ledger = make_strategy("realtime", store, registry, reply(fluency()))
    result = strategy.on_turn(make_turn(FLUENT + ", thanks"), NOW)
    slot = store.get_profile("u1")["hebrew_fluency"]
    assert (slot.value, slot.status, slot.evidence[0].quote) == ("fluent", "inferred", FLUENT)
    assert slot.evidence[0].message_id == "m1"
    assert result.llm_calls == 1 and len(llm.calls) == 1
    assert set(result.updated_slots) == {"hebrew_fluency", "preferred_language"}
    assert ledger.totals(group_by="strategy")["realtime"]["calls"] == 1


def test_preferred_language_comes_from_code_not_the_model(store, registry):
    wrong = {"slot": "preferred_language", "value": "he", "quote": "What is a sugya"}
    strategy, llm, _ = make_strategy("realtime", store, registry, reply(wrong))
    strategy.on_turn(make_turn("What is a sugya"), NOW)
    assert store.get_profile("u1")["preferred_language"].value == "en"
    assert "preferred_language" not in llm.calls[0]["system"]


def test_hebrew_prompt_sets_hebrew_language(store, registry):
    strategy, _, _ = make_strategy("realtime", store, registry)
    strategy.on_turn(make_turn("מה זה סוגיה בגמרא"), NOW)
    assert store.get_profile("u1")["preferred_language"].value == "he"


def test_model_sees_profile_slot_definitions_and_truncated_text(store, registry):
    store.set_stated("u1", "answer_style", "Be brief.", NOW)
    strategy, llm, _ = make_strategy("realtime", store, registry)
    strategy.on_turn(make_turn("Q" * 2000, reply="Z" * 2000), NOW)
    system, user = llm.calls[0]["system"], llm.calls[0]["user"]
    assert "hebrew_fluency" in system and "Allowed values: none, basic" in system
    assert '"answer_style"' in user and "stated" in user
    assert user.count("Q") == 1000 and user.count("Z") == 500


def test_stated_slot_is_never_overwritten(store, registry):
    store.set_stated("u1", "hebrew_fluency", "none", day(-5))
    strategy, _, _ = make_strategy("realtime", store, registry, reply(fluency()))
    result = strategy.on_turn(make_turn(FLUENT), NOW)
    slot = store.get_profile("u1")["hebrew_fluency"]
    assert (slot.value, slot.status) == ("none", "stated")
    assert "hebrew_fluency" not in result.updated_slots


def test_sensitive_turn_never_reaches_the_model_or_the_store(store, registry):
    strategy, llm, _ = make_strategy("realtime", store, registry, reply(fluency()))
    result = strategy.on_turn(make_turn("My mother died, I am sitting shiva. " + FLUENT), NOW)
    assert result.skipped_reason == "sensitive" and result.llm_calls == 0
    assert llm.calls == [] and store.get_profile("u1") == {}


def test_store_disabled_stores_nothing(store, registry):
    store.set_settings("u1", store_enabled=False)
    strategy, llm, _ = make_strategy("realtime", store, registry, reply(fluency()))
    result = strategy.on_turn(make_turn(FLUENT), NOW)
    assert result.skipped_reason == "store_disabled"
    assert llm.calls == [] and store.get_profile("u1") == {}


def test_invalid_enum_value_is_dropped(store, registry):
    strategy, _, _ = make_strategy("realtime", store, registry, reply(fluency(value="native")))
    strategy.on_turn(make_turn(FLUENT), NOW)
    assert "hebrew_fluency" not in store.get_profile("u1")


def test_fabricated_quote_drops_the_update(store, registry):
    strategy, _, _ = make_strategy(
        "realtime", store, registry, reply(fluency(quote="I grew up speaking Hebrew"))
    )
    strategy.on_turn(make_turn(FLUENT), NOW)
    assert "hebrew_fluency" not in store.get_profile("u1")


def test_unknown_slot_and_garbage_output_are_ignored(store, registry):
    bogus = {"slot": "favorite_color", "value": "blue", "quote": FLUENT}
    strategy, _, _ = make_strategy("realtime", store, registry, reply(bogus))
    strategy.on_turn(make_turn(FLUENT), NOW)
    strategy, _, _ = make_strategy("realtime", store, registry, lambda s, u: "not json")
    strategy.on_turn(make_turn(FLUENT, message="m2"), NOW)
    assert set(store.get_profile("u1")) == {"preferred_language"}


def test_shelf_life_sets_expiry_and_run_batch_only_expires(store, registry):
    update = {"slot": "text_categories", "value": ["Talmud"], "quote": "Berakhot"}
    strategy, llm, _ = make_strategy("realtime", store, registry, reply(update))
    strategy.on_turn(make_turn("Explain Berakhot 2a"), NOW)
    assert store.get_profile("u1")["text_categories"].expires_at == day(60)
    calls = len(llm.calls)
    result = strategy.run_batch(day(61))
    assert len(llm.calls) == calls and result.llm_calls == 0
    assert "text_categories" not in store.get_profile("u1")
