"""The shared arm pipeline: an arm replayed through the lab's replay loop, call labels, privacy, sampling."""

import json
from types import SimpleNamespace

from chat.V2.personalization.lab.cases.aggressiveness.arm_pipeline import (
    build,
    select_items,
)
from chat.V2.personalization.lab.replay import replay_persona
from chat.V2.personalization.llm import FakeLLMClient

LIGHT = {"model": "claude-haiku-4-5-20251001", "min_support": 3, "ratio": 2.0}
AGGRESSIVE = {"model": "claude-haiku-4-5-20251001", "min_support": 1, "ratio": 0.0}
SECRET = "My mother died last week and I am saying kaddish, which verses help?"


def persona(prompts, user_id="u:p"):
    turns = [
        {
            "id": f"t{i}",
            "user": text,
            "assistant": "ok",
            "at": f"2026-11-0{1 + i // 4}T10:0{i % 4}:00+00:00",
        }
        for i, text in enumerate(prompts)
    ]
    return {"id": "p", "user_id": user_id, "golden": {}, "checks": [],
            "sessions": [{"session_id": "s1", "turns": turns}]}  # fmt: skip


def llm_saying(by_prompt):
    """A model that answers hebrew_fluency by the latest prompt shown to it."""

    def respond(system, user):
        state = json.loads(user.split("<state>\n", 1)[1].split("\n</state>", 1)[0])
        choice = by_prompt.get(state["learner_prompts"][-1]["text"])
        if choice is None:
            return json.dumps({"answers": {}})
        return json.dumps({"answers": {"hebrew_fluency": {"choice": choice, "confidence": 0.9}}})

    return FakeLLMClient(respond)


PROMPTS = ["one", "two", "three", "four", "odd one"]
SAYS = {"one": "fluent", "two": "fluent", "three": "fluent", "four": "fluent", "odd one": "none"}


def fluency_by_turn(config):
    pipeline = build(config, llm_saying(SAYS))
    replay = replay_persona(pipeline, persona(PROMPTS))
    return (
        pipeline,
        replay,
        [(s["profile"].get("hebrew_fluency") or {}).get("value") for s in replay.turn_snapshots()],
    )


def test_light_fills_late_and_ignores_the_odd_prompt():
    _, _, seen = fluency_by_turn(LIGHT)
    assert seen == [None, None, "fluent", "fluent", "fluent"]


def test_aggressive_fills_at_once_and_flips_on_the_odd_prompt():
    _, _, seen = fluency_by_turn(AGGRESSIVE)
    assert seen == ["fluent", "fluent", "fluent", "fluent", "none"]


def test_every_model_call_is_a_typed_question_and_the_steps_are_in_the_catalog():
    from chat.V2.personalization.lab.steps import STEPS

    pipeline, replay, _ = fluency_by_turn(LIGHT)
    assert len(replay.calls) == 5
    assert {c["step"] for c in replay.calls} == {"typed_questions"}
    assert all(c["model"] == LIGHT["model"] and c["in_tokens"] > 0 for c in replay.calls)
    assert set(pipeline.steps()) <= set(STEPS)
    assert replay.step_counts["typed_questions"] == (5, 5)
    assert replay.step_counts["screen_sensitive"] == (5, 5)


def test_a_sensitive_turn_is_counted_dropped_and_never_sent():
    pipeline = build(AGGRESSIVE, llm_saying({}))
    replay = replay_persona(pipeline, persona([SECRET, "What does Rashi say?"]))
    assert replay.skips["sensitive"] == 1
    assert len(replay.calls) == 1
    assert not any("kaddish" in text for text in replay.sent_texts)
    assert replay.step_counts["screen_sensitive"] == (2, 1)


def test_a_stated_event_is_never_overwritten():
    pipeline = build(AGGRESSIVE, llm_saying(SAYS))
    p = persona(PROMPTS)
    p["stated_events"] = [{"after_turn": "t0", "slot": "hebrew_fluency", "value": "basic"}]
    replay = replay_persona(pipeline, p)
    assert replay.final_profile["hebrew_fluency"]["value"] == "basic"
    assert replay.final_profile["hebrew_fluency"]["status"] == "stated"


def test_build_needs_the_rule_in_the_config():
    import pytest

    with pytest.raises(KeyError):
        build({"model": "m"}, llm_saying({}))


def test_select_items_samples_by_seed_and_leaves_other_datasets_whole():
    items = [{"user_id": f"u{i:02d}"} for i in range(30)]
    config = {"sample_dataset": "anon-personas", "sample_size": 20, "sample_seed": 20261110}
    sampled = select_items(items, config, SimpleNamespace(name="anon-personas"))
    assert len(sampled) == 20 and sampled == select_items(
        items, config, SimpleNamespace(name="anon-personas")
    )
    assert [i["user_id"] for i in sampled] == sorted(i["user_id"] for i in sampled)
    assert select_items(items, config, SimpleNamespace(name="syn1")) == items
