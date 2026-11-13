"""The `extract` aspect's shared metrics: accuracy and over-inference on one slot group."""

import pytest

from chat.V2.personalization.lab.metrics import METRICS, RunData, evaluate, slot_accuracy
from chat.V2.personalization.lab.replay import PersonaReplay


def replay(golden, final):
    persona = {"id": "p", "user_id": "u:p", "golden": golden, "checks": [], "sessions": []}
    return PersonaReplay(persona, final_profile={k: {"value": v} for k, v in final.items()})


def value(name, group, replays):
    return evaluate(METRICS[name], RunData(replays=replays), group)


def test_the_aspect_metrics_are_registered_with_formulas_and_both_groups():
    for name in ("slot_accuracy_on", "over_inferred_on"):
        assert METRICS[name].formula and METRICS[name].params == ("language", "text_categories")


def test_slot_accuracy_on_scores_only_the_groups_slot():
    replays = [
        replay({"preferred_language": {"value": "en"}, "text_categories": {"must_include": ["Talmud"]}},
               {"preferred_language": "en", "text_categories": ["Tanakh"]}),
        replay({"preferred_language": {"value": "he"}, "text_categories": {"must_include": ["Talmud"]}},
               {"preferred_language": "en", "text_categories": ["Talmud", "Midrash"]}),
    ]  # fmt: skip
    assert value("slot_accuracy_on", "language", replays)[0] == 0.5
    assert value("slot_accuracy_on", "text_categories", replays)[0] == 0.5
    # the overall metric counts both slots, so it is the pooled figure of the two groups
    assert slot_accuracy(RunData(replays=replays)).value == 0.5


def test_slot_accuracy_on_skips_personas_without_a_golden_value_for_the_slot():
    replays = [
        replay({"preferred_language": None}, {"preferred_language": "en"}),
        replay({"preferred_language": {"value": "en"}}, {"preferred_language": "en"}),
    ]
    assert value("slot_accuracy_on", "language", replays)[0] == 1.0


def test_over_inferred_on_counts_fills_where_the_golden_slot_is_empty():
    replays = [
        replay({"text_categories": None}, {"text_categories": ["Talmud"]}),  # over-inferred
        replay({"text_categories": None}, {}),  # correctly empty
        replay({"text_categories": {"must_include": ["Talmud"]}}, {"text_categories": ["Talmud"]}),
    ]
    number, ratio = value("over_inferred_on", "text_categories", replays)
    assert (number, ratio.num, ratio.den) == (0.5, 1, 2)


def test_no_denominator_is_none_not_zero():
    replays = [replay({"preferred_language": {"value": "en"}}, {"preferred_language": "en"})]
    assert value("over_inferred_on", "language", replays)[0] is None


def test_an_unknown_group_is_refused():
    with pytest.raises(ValueError):
        METRICS["slot_accuracy_on"].compute(RunData(), "register")


# -- per prompt language ----------------------------------------------------------------------------


def lang_replay(prompts, golden, final):
    r = replay(golden, final)
    r.persona["sessions"] = [
        {"session_id": "s", "turns": [{"id": f"t{i}", "user": p} for i, p in enumerate(prompts)]}
    ]
    return r


def test_persona_language_is_hebrew_when_most_prompts_have_more_hebrew_than_latin_letters():
    from chat.V2.personalization.lab.cases.extract.metrics import persona_language

    he = {
        "sessions": [
            {
                "turns": [
                    {"user": "מה זה גמרא"},
                    {"user": "explain Rashi"},
                    {"user": "שלום, מה פירוש Talmud"},
                ]
            }
        ]
    }
    en = {
        "sessions": [{"turns": [{"user": "What is Berakhot 2a"}, {"user": "מה זה"}]}]
    }  # a tie is English
    assert persona_language(he) == "he" and persona_language(en) == "en"
    assert persona_language({"sessions": []}) == "en"


def test_by_language_metrics_split_the_personas_and_registered():
    for name in ("slot_accuracy_by_lang", "over_inferred_by_lang"):
        assert METRICS[name].formula and METRICS[name].params == ("he", "en")
    golden = {"preferred_language": {"value": "he"}, "hebrew_fluency": None}
    replays = [
        lang_replay(["שלום", "מה זה"], golden, {"preferred_language": "he"}),
        lang_replay(["שלום"], golden, {"preferred_language": "en", "hebrew_fluency": "basic"}),
        lang_replay(["hello"], golden, {"preferred_language": "he"}),
    ]  # fmt: skip
    assert value("slot_accuracy_by_lang", "he", replays)[0] == 0.5
    assert value("slot_accuracy_by_lang", "en", replays)[0] == 1.0
    he_filled = value("over_inferred_by_lang", "he", replays)[1]
    assert he_filled.num == 1  # the second persona's hebrew_fluency, where the golden is empty
    assert value("over_inferred_by_lang", "en", replays)[1].num == 0
    with pytest.raises(ValueError):
        METRICS["slot_accuracy_by_lang"].compute(RunData(), "fr")
