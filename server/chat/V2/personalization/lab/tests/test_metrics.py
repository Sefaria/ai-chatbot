import pytest

from chat.V2.personalization.lab import steps
from chat.V2.personalization.lab.metric_formulas import FORMULAS
from chat.V2.personalization.lab.metrics import METRICS, Ratio, RunData, evaluate
from chat.V2.personalization.lab.replay import PersonaReplay

GOLDEN = {
    "preferred_language": {"value": "en"},
    "hebrew_fluency": {"value": "none"},
    "terminology_comfort": {"value": "new"},
    "learning_habits": None,
}


def slot(value, status="inferred", evidence=()):
    return {"value": value, "status": status, "evidence": list(evidence)}


def snap(index, profile, kind="turn", turn_id=None):
    return {
        "index": index,
        "kind": kind,
        "turn_id": turn_id,
        "at": "t",
        "profile": profile,
        "proposals": [],
    }


def persona(**extra):
    turns = [
        {"id": f"t{i}", "at": "2026-11-01T09:00:00Z", "user": f"u{i}", "assistant": "a", "tags": []}
        for i in (1, 2, 3)
    ]
    return {
        "id": "p",
        "user_id": "u:p",
        "golden": GOLDEN,
        "checks": [],
        "sessions": [{"session_id": "s", "turns": turns}],
        **extra,
    }


def replay(finals, snapshots=None, **extra):
    """A PersonaReplay whose final profile is `finals` ({slot: value})."""
    profile = {k: slot(v) for k, v in finals.items()}
    snapshots = snapshots or [snap(i, profile, turn_id=f"t{i + 1}") for i in range(3)]
    return PersonaReplay(
        persona(**extra), snapshots, final_profile=snapshots[-1]["profile"], turns=3
    )


def value(name, data, variant=None):
    return evaluate(METRICS[name], data, variant)[0]


def test_every_metric_has_formula_text():
    assert len(METRICS) >= 45
    assert all(m.formula.strip() for m in METRICS.values())
    assert all(METRICS[name].formula == text for name, text in FORMULAS.items())


def test_formulas_are_the_test_trees_words():
    assert (
        FORMULAS["slot_accuracy"]
        == "slots whose final value equals the golden value ÷ slots that have a golden value"
    )
    assert FORMULAS["idempotent"].startswith("1 if a second job run")


def test_slot_accuracy_golden_is_one():
    golden = {"preferred_language": "en", "hebrew_fluency": "none", "terminology_comfort": "new"}
    assert value("slot_accuracy", RunData(replays=[replay(golden)])) == 1.0


def test_slot_accuracy_empty_profile_is_zero():
    assert value("slot_accuracy", RunData(replays=[replay({})])) == 0.0


def test_slot_accuracy_one_wrong_is_n_minus_one_over_n():
    wrong = {"preferred_language": "en", "hebrew_fluency": "fluent", "terminology_comfort": "new"}
    assert value("slot_accuracy", RunData(replays=[replay(wrong)])) == pytest.approx(2 / 3)


def test_slot_accuracy_is_a_ratio_so_it_can_be_pooled():
    result = evaluate(
        METRICS["slot_accuracy"], RunData(replays=[replay({"preferred_language": "en"})])
    )
    assert result[1] == Ratio(1, 3)


def test_over_inferred_counts_slots_the_golden_leaves_empty():
    from chat.V2.personalization.registry import SlotRegistry

    empty_slots = len(SlotRegistry.load().all()) - 3  # the golden above fills three slots
    data = RunData(replays=[replay({"learning_habits": "reads at night"})])
    assert value("over_inferred", data) == pytest.approx(1 / empty_slots)
    assert value("over_inferred", RunData(replays=[replay({})])) == 0.0


def test_wrong_slot_turns_ignores_empty_slots():
    empty = replay({}, [snap(i, {}, turn_id=f"t{i + 1}") for i in range(3)])
    assert value("wrong_slot_turns", RunData(replays=[empty])) == 0.0
    wrong_late = [
        snap(0, {}, turn_id="t1"),
        snap(1, {}, turn_id="t2"),
        snap(2, {"hebrew_fluency": slot("fluent")}, turn_id="t3"),
    ]
    assert value("wrong_slot_turns", RunData(replays=[replay({}, wrong_late)])) == pytest.approx(
        1 / 3
    )


def test_golden_timeline_used_when_present():
    timeline = [
        {"from_turn": 1, "slot": "hebrew_fluency", "value": "none"},
        {"from_turn": 3, "slot": "hebrew_fluency", "value": "basic"},
    ]
    held_none = [snap(i, {"hebrew_fluency": slot("none")}, turn_id=f"t{i + 1}") for i in range(3)]
    with_timeline = replay({}, held_none, golden_timeline=timeline)
    without = replay({}, held_none)
    # with the timeline, holding "none" is wrong only from turn 3; the final golden is "none"
    assert value("wrong_slot_turns", RunData(replays=[with_timeline])) == pytest.approx(1 / 3)
    assert value("wrong_slot_turns", RunData(replays=[without])) == 0.0


def test_flips_and_outlier_flips():
    tags = persona()
    tags["sessions"][0]["turns"][1]["tags"] = ["outlier"]
    snaps = [
        snap(0, {"preferred_language": slot("he")}, turn_id="t1"),
        snap(
            1, {"preferred_language": slot("en")}, turn_id="t2"
        ),  # a flip caused by the outlier turn
        snap(2, {"preferred_language": slot("he")}, turn_id="t3"),  # a flip, not tagged
    ]
    r = PersonaReplay(tags, snaps, final_profile=snaps[-1]["profile"], turns=3)
    data = RunData(replays=[r])
    assert value("flips_per_10_turns", data) == pytest.approx(2 / 3 * 10)
    assert value("outlier_flips", data) == 1


def test_stated_overwritten_counts_changes_to_a_slot_the_learner_set():
    events = [{"after_turn": "t1", "slot": "hebrew_fluency", "value": "fluent"}]
    snaps = [
        snap(0, {"hebrew_fluency": slot("fluent", "stated")}, turn_id="t1"),
        snap(1, {"hebrew_fluency": slot("none")}, turn_id="t2"),
        snap(2, {"hebrew_fluency": slot("none")}, turn_id="t3"),
    ]
    r = PersonaReplay(
        persona(stated_events=events), snaps, final_profile=snaps[-1]["profile"], turns=3
    )
    assert value("stated_overwritten", RunData(replays=[r])) == 1


def test_fill_rate_at_k_and_turns_to_fill():
    snaps = [
        snap(0, {}, turn_id="t1"),
        snap(1, {"hebrew_fluency": slot("none")}, turn_id="t2"),
        snap(2, {"hebrew_fluency": slot("none")}, turn_id="t3"),
    ]
    r = PersonaReplay(persona(), snaps, final_profile=snaps[-1]["profile"], turns=3)
    data = RunData(replays=[r])
    assert METRICS["fill_rate_at_k"].params == ("2", "4", "8")
    assert value("fill_rate_at_k", data, "2") == pytest.approx(1 / 3)  # 1 of 3 golden slots filled
    assert value("turns_to_fill", data) == 2


def test_run_agreement_needs_earlier_runs():
    r = replay({"hebrew_fluency": "none"})
    assert value("run_agreement", RunData(replays=[r])) is None
    same = {"u:p": {"hebrew_fluency": "none"}}
    other = {"u:p": {"hebrew_fluency": "basic"}}
    assert value("run_agreement", RunData(replays=[r], prior_profiles=[same, same])) == 1.0
    assert value("run_agreement", RunData(replays=[r], prior_profiles=[same, other])) == 0.0


def test_lead_over_next_is_worst_minus_best_other():
    runs = [
        {"slot_accuracy@a": 0.9, "slot_accuracy@b": 0.8},
        {"slot_accuracy@a": 0.88, "slot_accuracy@b": 0.82},
    ]
    data = RunData(prior_metrics=[runs[0]], metrics_so_far=runs[1])
    assert value("lead_over_next", data, "a") == pytest.approx(0.88 - 0.82)
    assert value("lead_over_next", data, "b") == pytest.approx(0.80 - 0.90)


def test_runs_identical():
    same = {"slot_accuracy": 0.5}
    assert value("runs_identical", RunData(prior_metrics=[same, same], metrics_so_far=same)) == 1.0
    assert (
        value(
            "runs_identical",
            RunData(prior_metrics=[same, {"slot_accuracy": 0.4}], metrics_so_far=same),
        )
        == 0.0
    )
    assert value("runs_identical", RunData(metrics_so_far=same)) is None


def test_label_metrics_count_wrong_not_unknown():
    labels = [
        ("academic", "academic"),
        ("yeshivish", "unknown"),
        ("none", "academic"),
        ("none", "none"),
    ]
    data = RunData(labels=labels)
    assert value("label_accuracy", data) == 0.5
    assert value("label_wrong", data) == 1
    assert value("coverage", data) == 0.75


def test_counted_metrics_read_the_pipelines_counts():
    data = RunData(counts={"prefers_personalized": 7, "prefers_personalized_of": 8, "intrusive": 1})
    assert value("prefers_personalized", data) == 0.875
    assert value("intrusive", data) == 1
    assert value("neutral_changed", data) is None


def test_cost_metrics_use_the_price_table():
    calls = [
        {
            "step": "extract_turn",
            "model": "claude-haiku-4-5",
            "in_tokens": 1_000_000,
            "out_tokens": 0,
        }
    ]
    data = RunData(calls=calls, counts={"turns": 1000})
    assert value("usd_per_1000_turns", data) == pytest.approx(1.0)
    assert value("calls_per_turn", data) == pytest.approx(1 / 1000)


def test_step_name_must_be_in_catalog():
    assert steps.check_step_name("screen_sensitive") == "screen_sensitive"
    with pytest.raises(ValueError, match="catalog"):
        steps.check_step_name("a free text step")


def _string_id_persona(timeline):
    turns = [
        {
            "id": f"u-t{i}",
            "at": "2026-11-01T09:00:00Z",
            "user": f"u{i}",
            "assistant": "a",
            "tags": [],
        }
        for i in range(1, 12)
    ]
    return {"golden": GOLDEN, "golden_timeline": timeline, "sessions": [{"turns": turns}]}


def test_golden_at_accepts_string_turn_ids():
    from chat.V2.personalization.lab.metrics import _SKIP, golden_at

    timeline = [
        {"from_turn": "u-t10", "slot": "hebrew_fluency", "value": "basic"},  # t10 sorts after t9
        {"from_turn": "u-t2", "slot": "hebrew_fluency", "value": "none"},
    ]
    persona_ = _string_id_persona(timeline)
    assert golden_at(persona_, "hebrew_fluency", 1) is _SKIP
    assert golden_at(persona_, "hebrew_fluency", 9) == {"value": "none"}
    assert golden_at(persona_, "hebrew_fluency", 10) == {"value": "basic"}
    assert golden_at(persona_, "hebrew_fluency", 11) == {"value": "basic"}


def test_wrong_slot_turns_with_string_turn_ids():
    timeline = [{"from_turn": "t3", "slot": "hebrew_fluency", "value": "basic"}]  # a turn id
    held_none = [snap(i, {"hebrew_fluency": slot("none")}, turn_id=f"t{i + 1}") for i in range(3)]
    with_ids = replay({}, held_none, golden_timeline=timeline)
    assert value("wrong_slot_turns", RunData(replays=[with_ids])) == pytest.approx(1 / 3)
