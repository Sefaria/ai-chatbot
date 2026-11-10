import json

import pytest

from chat.V2.personalization.jev import FakeJevClient, JevAnswer, JevError

from .conftest import NOW, day, make_strategy, make_turn
from .test_nightly import consolidation, log_turns, update

STYLE_TEXT = "From now on keep your answers short and give sources first."


def choice(value, confidence=0.9):
    return JevAnswer("choice", choice=value, confidence=confidence)


def jev_with(answers=None, instruction=None):
    """Jev fake: `answers` for the closed-set request, `instruction` (style, exclude) for the other."""
    answers = answers or {}

    def respond(state, questions):
        if "message" in state:
            style, exclude = instruction or (0.0, 0.0)
            return {
                "is_lasting_style_preference": JevAnswer("noul", noul=style),
                "asks_to_exclude_sources": JevAnswer("noul", noul=exclude),
            }
        return dict(answers)

    return FakeJevClient(respond)


def strategy(store, registry, jev, responder=lambda s, u: "{}"):
    return make_strategy("hybrid_jev", store, registry, responder, jev=jev)


def test_sets_empty_slots_in_one_request_per_turn(store, registry):
    jev = jev_with(
        {
            "hebrew_fluency": choice("none", 0.55),
            "terminology_comfort": choice("new", 0.9),
            "category:Tanakh": JevAnswer("noul", noul=0.8),
            "category:Talmud": JevAnswer("noul", noul=0.9),
            "category:Midrash": JevAnswer("noul", noul=0.2),
        }
    )
    s, llm, ledger = strategy(store, registry, jev)
    result = s.on_turn(make_turn("What does Rashi mean? I can't read Hebrew."), NOW)
    profile = store.get_profile("u1")
    assert (
        profile["hebrew_fluency"].value == "none" and profile["terminology_comfort"].value == "new"
    )
    assert profile["text_categories"].value == ["Talmud", "Tanakh"]
    assert profile["text_categories"].expires_at == day(60)
    assert (
        profile["hebrew_fluency"].evidence[0].quote == "What does Rashi mean? I can't read Hebrew."
    )
    assert {"preferred_language", "hebrew_fluency", "text_categories"} <= set(result.updated_slots)
    assert len(jev.calls) == 1 and llm.calls == [] and result.llm_calls == 0
    entry = ledger.entries[0]
    assert (entry.model, entry.phase, entry.output_tokens) == ("jev-1.13.0", "turn", 0)
    assert entry.input_tokens > 0


def test_request_state_questions_and_window(store, registry):
    jev = jev_with()
    s, _, _ = strategy(store, registry, jev)
    for i in range(15):
        s.on_turn(
            make_turn(
                f"prompt {i} " + "x" * 600, session=f"s{i // 5}", message=f"m{i}", at=day(-1)
            ),
            day(-1),
        )
    state, questions = jev.calls[-1]
    prompts = state["learner_prompts"]
    assert len(prompts) == 12 and prompts[-1]["text"].startswith("prompt 14")
    assert all(len(p["text"]) == 500 for p in prompts)
    assert {p["session"] for p in prompts} == {1, 2, 3} and prompts[0]["date"] == "2026-11-09"
    assert set(questions) >= {"hebrew_fluency", "terminology_comfort", "category:Tanakh"}


@pytest.mark.parametrize(
    "answer,expected",
    [
        (choice("unclear", 1.0), None),
        (choice("basic", 0.49), None),
        (choice("basic", 0.5), "basic"),
        (JevAnswer("choice", choice="native", confidence=0.99), None),
        (JevAnswer("choice", choice="basic"), None),
    ],
)
def test_empty_slot_threshold_and_unclear(store, registry, answer, expected):
    s, _, _ = strategy(store, registry, jev_with({"hebrew_fluency": answer}))
    s.on_turn(make_turn("hello there"), NOW)
    slot = store.get_profile("u1").get("hebrew_fluency")
    assert (slot.value if slot else None) == expected


def test_change_of_an_existing_value_needs_the_higher_threshold(store, registry):
    store.set_inferred("u1", "hebrew_fluency", "none", [], day(-5))
    s, _, _ = strategy(store, registry, jev_with({"hebrew_fluency": choice("fluent", 0.7)}))
    s.on_turn(make_turn("hello"), NOW)
    assert store.get_profile("u1")["hebrew_fluency"].value == "none"
    s, _, _ = strategy(store, registry, jev_with({"hebrew_fluency": choice("fluent", 0.75)}))
    s.on_turn(make_turn("hello", message="m2"), NOW)
    assert store.get_profile("u1")["hebrew_fluency"].value == "fluent"


def test_stated_slot_is_never_overwritten(store, registry):
    store.set_stated("u1", "hebrew_fluency", "none", day(-5))
    s, _, _ = strategy(store, registry, jev_with({"hebrew_fluency": choice("fluent", 1.0)}))
    result = s.on_turn(make_turn("hello"), NOW)
    assert store.get_profile("u1")["hebrew_fluency"].status == "stated"
    assert "hebrew_fluency" not in result.updated_slots


def test_categories_need_the_noul_threshold_and_keep_the_top_three(store, registry):
    def noul(**scores):
        return {f"category:{k}": JevAnswer("noul", noul=v) for k, v in scores.items()}

    s, _, _ = strategy(store, registry, jev_with(noul(Tanakh=0.64, Talmud=0.5)))
    s.on_turn(make_turn("hello"), NOW)
    assert "text_categories" not in store.get_profile("u1")  # nothing reaches 0.65
    answers = noul(Tanakh=0.65, Talmud=0.9, Midrash=0.7, Musar=0.8, Liturgy=0.4)
    s, _, _ = strategy(store, registry, jev_with(answers))
    s.on_turn(make_turn("hello", message="m2"), NOW)
    assert store.get_profile("u1")["text_categories"].value == ["Talmud", "Musar", "Midrash"]


def test_a_noul_in_the_uncertain_band_does_not_act(store, registry):
    answers = {f"category:{c}": JevAnswer("noul", noul=0.5) for c in ("Tanakh", "Talmud")}
    s, _, _ = strategy(store, registry, jev_with(answers))
    s.on_turn(make_turn("hello"), NOW)
    assert "text_categories" not in store.get_profile("u1")


def test_preferred_language_is_set_in_code_without_jev(store, registry):
    s, _, _ = strategy(store, registry, jev_with())
    s.on_turn(make_turn("מה זה סוגיה בגמרא"), NOW)
    assert store.get_profile("u1")["preferred_language"].value == "he"


def test_sensitive_turn_never_reaches_jev_or_the_store(store, registry):
    jev = jev_with({"hebrew_fluency": choice("fluent", 1.0)})
    s, llm, _ = strategy(store, registry, jev)
    result = s.on_turn(make_turn("My mother died, I am sitting shiva. From now on be gentle."), NOW)
    assert result.skipped_reason == "sensitive"
    assert jev.calls == [] and llm.calls == [] and store.observations("u1") == []
    s.on_turn(make_turn("Explain Berakhot", message="m2"), NOW)
    assert all("shiva" not in json.dumps(state) for state, _ in jev.calls)


def test_store_disabled_makes_no_jev_request(store, registry):
    store.set_settings("u1", store_enabled=False)
    jev = jev_with()
    result = strategy(store, registry, jev)[0].on_turn(make_turn("hello"), NOW)
    assert result.skipped_reason == "store_disabled" and jev.calls == []


def test_jev_error_is_swallowed_and_counted(store, registry):
    def boom(state, questions):
        raise JevError("Jev HTTP 500")

    s, _, ledger = strategy(store, registry, FakeJevClient(boom))
    result = s.on_turn(make_turn(STYLE_TEXT), NOW)
    assert result.errors == 2 and result.proposals == []  # closed-set and instruction requests
    assert len(store.observations("u1")) == 1 and ledger.entries == []
    assert store.get_profile("u1")["preferred_language"].value == "en"  # the code path still ran


def test_lasting_style_instruction_becomes_a_pending_proposal(store, registry):
    jev = jev_with(instruction=(0.95, 0.02))
    s, _, ledger = strategy(store, registry, jev)
    result = s.on_turn(make_turn("Too long. " + STYLE_TEXT), NOW)
    (proposal,) = result.proposals
    assert (proposal.slot_key, proposal.value, proposal.status) == (
        "answer_style",
        STYLE_TEXT,
        "pending",
    )
    assert "answer_style" not in store.get_profile("u1")
    assert len(jev.calls) == 2 and jev.calls[0][0] == {"message": STYLE_TEXT}
    assert len(ledger.entries) == 2


def test_long_instruction_is_truncated_by_the_registry(store, registry):
    jev = jev_with(instruction=(0.95, 0.0))
    s, _, _ = strategy(store, registry, jev)
    result = s.on_turn(make_turn("From now on " + "keep it short " * 40), NOW)
    assert len(result.proposals[0].value) == 200


def test_exclude_sources_instruction_is_never_stored(store, registry):
    jev = jev_with(instruction=(0.95, 0.97))
    s, _, _ = strategy(store, registry, jev)
    result = s.on_turn(
        make_turn("From now on, never show me anything written by Reform rabbis."), NOW
    )
    assert result.skipped_reason == "instruction_excludes_sources" and result.proposals == []
    assert store.proposals("u1") == []


@pytest.mark.parametrize("scores", [(0.64, 0.0), (0.5, 0.5), (0.0, 0.0)])
def test_uncertain_or_unrelated_instruction_needs_settings(store, registry, scores):
    s, _, _ = strategy(store, registry, jev_with(instruction=scores))
    result = s.on_turn(make_turn("Always bring me coffee."), NOW)
    assert result.skipped_reason == "instruction_needs_settings" and store.proposals("u1") == []


def test_no_instruction_means_one_request(store, registry):
    jev = jev_with()
    strategy(store, registry, jev)[0].on_turn(make_turn("What does Rashi say?"), NOW)
    assert len(jev.calls) == 1


def test_batch_touches_only_the_text_slots(store, registry):
    q1, q2 = "keep answers short please", "short answers again"
    updates = [
        update("answer_style", "Keep answers short.", ("m1", q1), ("m2", q2)),
        update("hebrew_fluency", "fluent", ("m1", q1), ("m2", q2)),
        update("text_categories", ["Talmud"], ("m1", q1)),
    ]
    s, llm, _ = strategy(
        store,
        registry,
        jev_with(),
        consolidation(*updates, clear=["hebrew_fluency", "learning_habits"]),
    )
    store.set_inferred("u1", "hebrew_fluency", "basic", [], day(-9))
    log_turns(s, [("s1", "m1", q1, day(-2)), ("s2", "m2", q2, day(-1))])
    result = s.run_batch(NOW)
    profile = store.get_profile("u1")
    assert profile["answer_style"].value == "Keep answers short."
    assert profile["hebrew_fluency"].value == "basic" and "text_categories" not in profile
    system = llm.calls[0]["system"]
    assert "answer_style" in system and "learning_habits" in system
    assert "hebrew_fluency (enum)" not in system and "text_categories (list)" not in system
    assert result.llm_calls == 1


def test_batch_skips_slots_with_a_pending_proposal(store, registry):
    store.add_proposal("u1", "answer_style", "Be brief.", "q", NOW)
    upd = update("answer_style", "Be long.", ("m1", "one"), ("m2", "two"))
    s, _, _ = strategy(store, registry, jev_with(), consolidation(upd))
    log_turns(s, [("s1", "m1", "one", day(-2)), ("s2", "m2", "two", day(-1))])
    s.run_batch(NOW)
    assert "answer_style" not in store.get_profile("u1")
