import json

from chat.V2.personalization.strategies.hybrid import (
    PROPOSE_MEMORY_UPDATE_TOOL,
    handle_propose_memory_update,
)

from .conftest import NOW, day, make_strategy, make_turn
from .test_nightly import Q1, Q2, clear_item, consolidation, log_turns, update

INSTRUCTION = "From now on keep your answers short."


def mapper(slot, value=None):
    return lambda system, user: json.dumps(
        {"slot": slot, "value": value, "excludes_sources": False}
    )


def test_language_is_set_in_code_without_a_model_call(store, registry):
    strategy, llm, _ = make_strategy("hybrid", store, registry)
    result = strategy.on_turn(make_turn("What is a sugya?", session="s1", at=day(-2)), day(-2))
    assert result.updated_slots == ["preferred_language"] and result.llm_calls == 0
    assert llm.calls == [] and len(store.observations("u1")) == 1
    assert store.get_profile("u1")["preferred_language"].value == "en"
    strategy.on_turn(make_turn("מה זה סוגיה", session="s2", message="m2"), NOW)
    assert store.get_profile("u1")["preferred_language"].value == "en"  # one Hebrew session
    strategy.on_turn(make_turn("מה זה גמרא", session="s3", message="m3"), NOW)
    assert store.get_profile("u1")["preferred_language"].value == "he"  # two in a row


def test_standing_instruction_becomes_a_pending_proposal_not_a_write(store, registry):
    strategy, llm, ledger = make_strategy(
        "hybrid", store, registry, mapper("answer_style", "Keep answers short.")
    )
    result = strategy.on_turn(make_turn(INSTRUCTION), NOW)
    assert result.llm_calls == 1 and result.skipped_reason is None
    (proposal,) = result.proposals
    assert (proposal.slot_key, proposal.value, proposal.status) == (
        "answer_style",
        "Keep answers short.",
        "pending",
    )
    assert proposal.quote == INSTRUCTION
    assert "answer_style" not in store.get_profile("u1")
    assert len(ledger.entries) == 1 and "preferred_language" in llm.calls[0]["system"]
    store.resolve_proposal("u1", proposal.id, True, NOW)
    assert store.get_profile("u1")["answer_style"].status == "stated"


def test_unmapped_instruction_needs_settings(store, registry):
    strategy, _, _ = make_strategy(
        "hybrid", store, registry, lambda s, u: '{"slot": null, "excludes_sources": false}'
    )
    result = strategy.on_turn(make_turn("Always be nice to my cat"), NOW)
    assert result.skipped_reason == "instruction_needs_settings" and result.proposals == []
    assert store.proposals("u1") == []
    result = make_strategy("hybrid", store, registry, mapper("hebrew_fluency", "native"))[
        0
    ].on_turn(make_turn(INSTRUCTION, message="m2"), NOW)
    assert result.skipped_reason == "instruction_needs_settings"


def test_turn_without_instruction_makes_no_model_call(store, registry):
    strategy, llm, _ = make_strategy("hybrid", store, registry)
    strategy.on_turn(make_turn("What does Rashi say?"), NOW)
    assert llm.calls == []


def test_sensitive_turn_with_instruction_is_dropped_before_the_model(store, registry):
    strategy, llm, _ = make_strategy("hybrid", store, registry, mapper("answer_style", "x"))
    result = strategy.on_turn(make_turn("My father died. From now on be gentle."), NOW)
    assert result.skipped_reason == "sensitive" and llm.calls == []
    assert store.observations("u1") == [] and store.proposals("u1") == []


def test_stated_language_survives_the_in_code_update(store, registry):
    store.set_stated("u1", "preferred_language", "he", day(-1))
    strategy, _, _ = make_strategy("hybrid", store, registry)
    result = strategy.on_turn(make_turn("What is a sugya?"), NOW)
    assert result.updated_slots == []
    assert store.get_profile("u1")["preferred_language"].value == "he"


def test_batch_enforces_min_sessions_like_nightly(store, registry):
    upd = update("hebrew_fluency", "basic", ("m1", Q1), ("m2", Q2))
    strategy, _, _ = make_strategy("hybrid", store, registry, consolidation(upd))
    log_turns(strategy, [("s1", "m1", Q1, day(-2)), ("s1", "m2", Q2, day(-1))])
    strategy.run_batch(NOW)
    assert "hebrew_fluency" not in store.get_profile("u1")
    log_turns(strategy, [("s2", "m3", "another one", day(-1))])
    strategy, _, _ = make_strategy(
        "hybrid",
        store,
        registry,
        consolidation(update("hebrew_fluency", "basic", ("m1", Q1), ("m3", "another one"))),
    )
    strategy.run_batch(NOW)
    assert store.get_profile("u1")["hebrew_fluency"].value == "basic"


def test_batch_skips_slots_with_a_pending_proposal(store, registry):
    store.set_inferred("u1", "hebrew_fluency", "none", [], day(-9))
    store.add_proposal("u1", "hebrew_fluency", "fluent", "I am fluent", NOW)
    upd = update("hebrew_fluency", "basic", ("m1", Q1), ("m2", Q2))
    strategy, _, _ = make_strategy(
        "hybrid", store, registry, consolidation(upd, clear=[clear_item("hebrew_fluency")])
    )
    log_turns(strategy, [("s1", "m1", Q1, day(-2)), ("s2", "m2", Q2, day(-1))])
    strategy.run_batch(NOW)
    assert store.get_profile("u1")["hebrew_fluency"].value == "none"


def test_propose_tool_schema_has_the_tool_schemas_shape(registry):
    from chat.V2.agent.tool_schemas import TOOL_GET_TEXT

    assert set(PROPOSE_MEMORY_UPDATE_TOOL) == set(TOOL_GET_TEXT)
    schema = PROPOSE_MEMORY_UPDATE_TOOL["input_schema"]
    assert schema["required"] == ["slot", "value", "quote"]
    assert schema["properties"]["slot"]["enum"] == [s.key for s in registry.all()]
    from chat.V2.agent.tool_schemas import ALL_TOOLS

    assert "propose_memory_update" not in ALL_TOOLS


def test_handle_propose_memory_update_validates_and_dedupes(store, registry):
    propose = lambda *a: handle_propose_memory_update(store, registry, "u1", *a, NOW)  # noqa: E731
    assert propose("answer_style", "", "quote") is None
    assert propose("nope", "x", "quote") is None
    assert propose("hebrew_fluency", "native", "quote") is None
    assert propose("answer_style", "Be brief.", "  ") is None
    first = propose("answer_style", "Be brief.", "keep it short")
    assert first.status == "pending"
    assert propose("answer_style", "Be brief.", "keep it short") == first
    assert len(store.proposals("u1")) == 1
    store.set_stated("u1", "hebrew_fluency", "fluent", NOW)
    assert propose("hebrew_fluency", "fluent", "I am fluent") is None
    assert propose("hebrew_fluency", "basic", "actually basic") is not None
    assert propose("hebrew_fluency", "basic", "x").user_id == "u1"


def test_hybrid_proposal_cannot_set_deterministic_slot(store, registry):
    assert registry.get("register").deterministic
    strategy, _, _ = make_strategy("hybrid", store, registry, mapper("register", "academic"))
    result = strategy.on_turn(make_turn(INSTRUCTION), NOW)
    assert result.proposals == []
    assert store.proposals("u1", "pending") == []
    assert "register" not in store.get_profile("u1")
