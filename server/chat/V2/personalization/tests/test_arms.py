"""The update-rule arms of the `aggressiveness` aspect: `decide`, `SupportCounter`, `ArmUpdater`."""

from chat.V2.personalization.arms import ArmUpdater, SupportCounter, decide
from chat.V2.personalization.store import InMemoryProfileStore
from chat.V2.personalization.typed import TypedAnswer

from .conftest import NOW, day, make_turn

LIGHT = {"min_support": 3, "ratio": 2.0}
AGGRESSIVE = {"min_support": 1, "ratio": 0.0}


# -- decide -----------------------------------------------------------------------------------


def test_four_against_one_activates():
    assert decide({"fluent": 4, "basic": 1}, None, **LIGHT) == "fluent"


def test_two_against_two_does_not():
    assert decide({"fluent": 2, "basic": 2}, None, **LIGHT) is None
    assert decide({"fluent": 3, "basic": 3}, None, **LIGHT) is None  # a tie never decides


def test_below_min_support_does_not():
    assert decide({"fluent": 2}, None, **LIGHT) is None
    assert decide({"fluent": 3}, None, **LIGHT) == "fluent"


def test_a_rival_reaching_the_threshold_flips_and_not_before():
    # the incumbent has 4; the rival needs at least 3 signals and twice the incumbent's 4
    assert decide({"fluent": 4, "basic": 7}, "fluent", **LIGHT) is None
    assert decide({"fluent": 4, "basic": 8}, "fluent", **LIGHT) == "basic"
    # an incumbent that is also the leader changes nothing
    assert decide({"fluent": 5, "basic": 1}, "fluent", **LIGHT) is None


def test_incumbent_without_counts_counts_as_zero():
    assert decide({"basic": 3}, "fluent", **LIGHT) == "basic"


def test_aggressive_takes_any_single_signal_and_a_tie_decides_nothing():
    assert decide({"basic": 1}, "fluent", **AGGRESSIVE) == "basic"
    assert decide({"fluent": 1, "basic": 1}, "fluent", **AGGRESSIVE) is None


# -- SupportCounter ---------------------------------------------------------------------------


def test_counter_counts_per_slot_and_value():
    counter = SupportCounter()
    for value in ("a", "a", "b"):
        counter.add("register", value)
    counter.add("hebrew_fluency", "none")
    assert counter.counts("register") == {"a": 2, "b": 1}
    assert counter.counts("hebrew_fluency") == {"none": 1}
    assert counter.counts("terminology_comfort") == {}


def test_counter_latest_only_keeps_the_latest_signal():
    counter = SupportCounter(latest_only=True)
    counter.add("register", "academic")
    counter.add("register", "academic")
    counter.add("register", "traditional")
    assert counter.counts("register") == {"traditional": 1}
    counter.add("text_categories", [])  # an empty signal changes nothing
    assert counter.counts("text_categories") == {}


# -- ArmUpdater -------------------------------------------------------------------------------


class ScriptedTyped:
    """A typed client that answers from a per-call script and records every state it was shown."""

    def __init__(self, script):
        self.script = list(script)
        self.states: list[str] = []
        self.keys: list[list[str]] = []

    def answer(self, state, questions):
        self.states.append(state)
        self.keys.append([q.key for q in questions])
        spec = self.script.pop(0) if self.script else {}
        out = []
        for q in questions:
            given = spec.get(q.key)
            if given is None:
                out.append(TypedAnswer(q.key))
            elif q.kind == "noul":
                out.append(TypedAnswer(q.key, noul=given))
            else:
                out.append(TypedAnswer(q.key, choice=given, confidence=0.9))
        return out


def run(arm, typed_script, texts=None, user="u1", store=None):
    store = store or InMemoryProfileStore()
    typed = ScriptedTyped(typed_script)
    updater = ArmUpdater(store, typed, **arm)
    texts = texts or ["How do I read this?"] * len(typed_script)
    history = []
    for i, text in enumerate(texts):
        turn = make_turn(text, user=user, message=f"m{i}", at=day(0))
        updater.on_turn(user, turn)
        history.append(store.get_profile(user).get("hebrew_fluency"))
    return store, typed, updater, history


def value_of(history, i):
    return history[i].value if history[i] else None


def test_light_four_against_one_activates_the_first():
    script = (
        [{"hebrew_fluency": "fluent"}] * 2
        + [{"hebrew_fluency": "basic"}]
        + [{"hebrew_fluency": "fluent"}] * 2
    )
    _, _, _, history = run(LIGHT, script)
    # 3 signals for fluent against 1 is already 3 and twice the rival: active at the fourth turn
    assert [value_of(history, i) for i in range(5)] == [None, None, None, "fluent", "fluent"]


def test_light_two_against_two_activates_neither_then_four_more_flip_it():
    script = [{"hebrew_fluency": v} for v in ("fluent", "basic", "fluent", "basic")]
    _, _, _, history = run(LIGHT, script)
    assert all(h is None for h in history)

    script += [{"hebrew_fluency": "basic"}] * 4  # basic 6 against fluent 2: activates
    _, _, _, history = run(LIGHT, script)
    assert value_of(history, 7) == "basic"


def test_light_flips_only_when_a_rival_reaches_the_threshold():
    script = [{"hebrew_fluency": "fluent"}] * 4 + [{"hebrew_fluency": "basic"}] * 8
    _, _, _, history = run(LIGHT, script)
    assert value_of(history, 3) == "fluent"
    assert value_of(history, 10) == "fluent"  # basic has 7 against fluent's 4
    assert value_of(history, 11) == "basic"  # basic has 8: twice 4


def test_light_one_odd_prompt_changes_nothing():
    script = (
        [{"hebrew_fluency": "fluent"}] * 3
        + [{"hebrew_fluency": "none"}]
        + [{"hebrew_fluency": "fluent"}]
    )
    _, _, updater, history = run(LIGHT, script)
    assert {value_of(history, i) for i in (2, 3, 4)} == {"fluent"}
    assert updater.stats["flips"] == 0  # language and fluency never changed value


def test_aggressive_latest_signal_wins():
    script = [
        {"hebrew_fluency": "fluent"},
        {"hebrew_fluency": "fluent"},
        {"hebrew_fluency": "none"},  # one contradicting prompt replaces it
        {},  # no signal: nothing changes
        {"hebrew_fluency": "fluent"},
    ]
    _, _, _, history = run(AGGRESSIVE, script)
    assert [value_of(history, i) for i in range(5)] == [
        "fluent",
        "fluent",
        "none",
        "none",
        "fluent",
    ]


def test_aggressive_fills_on_the_first_confident_prompt():
    _, _, _, history = run(AGGRESSIVE, [{"hebrew_fluency": "basic"}])
    assert value_of(history, 0) == "basic"


def test_an_unclear_or_unsure_answer_is_no_signal():
    class Unsure(ScriptedTyped):
        def answer(self, state, questions):
            return [
                TypedAnswer(q.key, choice="unclear", confidence=0.9)
                if q.kind == "choice"
                else TypedAnswer(q.key, noul=0.4)
                for q in questions
            ]

    store = InMemoryProfileStore()
    updater = ArmUpdater(store, Unsure([]), **AGGRESSIVE)
    updater.on_turn("u1", make_turn("hello there", message="m1"))
    profile = store.get_profile("u1")
    assert "hebrew_fluency" not in profile and "text_categories" not in profile


def test_language_comes_from_the_script_and_categories_from_the_questions():
    store, _, _, _ = run(
        AGGRESSIVE,
        [{"category:Talmud": 0.9, "category:Tanakh": 0.8, "category:Midrash": 0.1}],
    )
    profile = store.get_profile("u1")
    assert profile["preferred_language"].value == "en"
    assert profile["text_categories"].value == ["Tanakh", "Talmud"]


def test_light_categories_need_min_support():
    script = [{"category:Talmud": 0.9}] * 2 + [{"category:Talmud": 0.9, "category:Tanakh": 0.9}]
    store, _, _, _ = run(LIGHT, script)
    assert store.get_profile("u1")["text_categories"].value == ["Talmud"]


def test_a_slot_the_learner_set_never_changes():
    store = InMemoryProfileStore()
    store.set_stated("u1", "hebrew_fluency", "none", NOW)
    store.set_stated("u1", "preferred_language", "he", NOW)
    script = [{"hebrew_fluency": "fluent"}] * 6
    for arm in (LIGHT, AGGRESSIVE):
        typed = ScriptedTyped(script)
        updater = ArmUpdater(store, typed, **arm)
        for i in range(6):
            updater.on_turn("u1", make_turn("Peshat of this verse?", message=f"m{i}", at=day(i)))
        profile = store.get_profile("u1")
        assert (
            profile["hebrew_fluency"].value == "none"
            and profile["hebrew_fluency"].status == "stated"
        )
        assert profile["preferred_language"].value == "he"
        assert all("hebrew_fluency" not in keys for keys in typed.keys)  # not even asked
        assert updater.stats["writes"] <= 1  # only text_categories or register could be written


def test_a_sensitive_turn_never_reaches_the_typed_client_or_the_store():
    store = InMemoryProfileStore()
    typed = ScriptedTyped([{"hebrew_fluency": "fluent"}] * 3)
    updater = ArmUpdater(store, typed, **AGGRESSIVE)
    secret = "I have been diagnosed with cancer and my doctor says I should pray more"
    assert updater.on_turn("u1", make_turn(secret, message="m1")) == []
    assert typed.states == []
    assert store.get_profile("u1") == {}
    assert updater.stats["skipped_sensitive"] == 1
    updater.on_turn("u1", make_turn("What does Rashi say?", message="m2"))
    assert len(typed.states) == 1 and secret not in typed.states[0]


def test_only_the_latest_prompt_is_shown_and_users_are_counted_apart():
    store = InMemoryProfileStore()
    typed = ScriptedTyped([{"hebrew_fluency": "fluent"}] * 4)
    updater = ArmUpdater(store, typed, **LIGHT)
    for i, user in enumerate(("a", "b", "a", "b")):
        updater.on_turn(user, make_turn(f"prompt {i}", user=user, message=f"m{i}"))
    assert "prompt 0" not in typed.states[2] and "prompt 2" in typed.states[2]
    assert "hebrew_fluency" not in store.get_profile("a")  # two signals each: below 3
    assert updater.counter("a").counts("hebrew_fluency") == {"fluent": 2}
