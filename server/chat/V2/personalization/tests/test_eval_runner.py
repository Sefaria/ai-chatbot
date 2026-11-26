"""The simulated clock, auto-accept, recording client and the end-to-end evaluators."""

import json
from datetime import UTC, datetime

from chat.V2.personalization.evals import run_impact_eval, run_memory_eval
from chat.V2.personalization.evals.oracle import oracle_responder
from chat.V2.personalization.evals.runner import RecordingLLMClient, run_persona
from chat.V2.personalization.evals.scoring import evaluate_check
from chat.V2.personalization.llm import FakeLLMClient


def persona(turns, **extra):
    """turns: list of (id, session, iso time, text)."""
    sessions: dict[str, list] = {}
    for tid, session, at, text in turns:
        sessions.setdefault(session, []).append(
            {"id": tid, "at": at, "user": text, "assistant": "ok", **({"tags": []})}
        )
    return {
        "id": "p", "user_id": "u:p", "trap": "x", "interview": None,
        "sessions": [{"session_id": s, "turns": t} for s, t in sessions.items()],
        "golden": {"hebrew_fluency": {"value": "basic"}, "text_categories": {"must_include": ["Talmud"]}},
        "checks": [], **extra,
    }  # fmt: skip


TURNS = [
    ("t1", "s1", "2026-11-01T09:00:00Z", "Explain Berakhot 2a, I read Hebrew slowly"),
    ("t2", "s1", "2026-11-01T22:30:00Z", "And Berakhot 2b please"),
    ("t3", "s2", "2026-11-03T08:00:00Z", "More Berakhot, I read Hebrew slowly"),
]


def run(strategy, p, responder=None):
    from chat.V2.personalization.registry import SlotRegistry

    llm = FakeLLMClient(responder or oracle_responder(p))
    return run_persona(strategy, p, llm, "claude-haiku-4-5", SlotRegistry.load())


def test_batch_runs_once_per_day_boundary_and_once_at_the_end():
    result = run("nightly", persona(TURNS))
    assert result.batches == [
        datetime(2026, 11, 2, 3, tzinfo=UTC),
        datetime(2026, 11, 4, 3, tzinfo=UTC),
    ]
    assert [s["kind"] for s in result.snapshots] == ["turn", "turn", "batch", "turn", "batch"]
    assert result.turns == 3


def test_a_turn_snapshot_precedes_the_batch_that_consolidates_it():
    result = run("nightly", persona(TURNS))
    assert result.snapshots[0]["profile"] == {} and result.snapshots[1]["profile"] == {}
    assert "text_categories" in result.snapshots[2]["profile"]  # the day-boundary batch
    assert result.snapshots[2]["at"].startswith("2026-11-02T03:00")


def test_realtime_has_the_slot_in_the_turn_snapshot():
    result = run("realtime", persona(TURNS))
    assert result.snapshots[0]["profile"]["hebrew_fluency"]["value"] == "basic"
    assert {e.phase for e in result.ledger_entries} == {"turn"}


def test_hybrid_auto_accepts_proposals_and_counts_them():
    turns = [("t1", "s1", "2026-11-01T09:00:00Z", "From now on keep answers short.")]
    p = persona(turns)
    p["golden"]["answer_style"] = {"any_of": ["short"]}
    result = run("hybrid", p)
    assert result.auto_accepted == 1
    slot = result.snapshots[0]["profile"]["answer_style"]
    assert (slot["status"], slot["value"]) == ("stated", "short")
    assert result.snapshots[0]["proposals"][0]["status"] == "accepted"


def test_stated_events_and_interview():
    p = persona(TURNS, stated_events=[{"after_turn": "t2", "slot": "hebrew_fluency", "value": "fluent"}],
                interview={"terminology_comfort": "new"})  # fmt: skip
    result = run("nightly", p)
    assert result.snapshots[0]["profile"].get("hebrew_fluency") is None
    assert result.snapshots[1]["profile"]["hebrew_fluency"]["status"] == "stated"
    assert result.final_profile["hebrew_fluency"]["value"] == "fluent"
    explicit = run("explicit", p)
    assert explicit.snapshots[0]["profile"]["terminology_comfort"]["status"] == "stated"
    assert explicit.skips["explicit_only"] == 3 and explicit.ledger_entries == []


def test_recording_client_keeps_every_text_sent():
    recorder = RecordingLLMClient(FakeLLMClient(lambda s, u: "ok"))
    recorder.complete(system="SYS", user="USR", model="m")
    assert recorder.sent == [("SYS", "USR")] and recorder.sent_texts == ["SYS", "USR"]
    assert len(recorder.results) == 1


def test_never_stored_all_catches_a_phrase_sent_to_the_model():
    turns = [
        (
            "t1",
            "s1",
            "2026-11-01T09:00:00Z",
            "Tell me about Berakhot in general terms, with all the usual detail please "
            + "and then also bluepelican",
        )
    ]
    result = run("realtime", persona(turns), responder=lambda s, u: '{"updates": []}')
    scope_all = {"type": "never_stored", "phrases": ["bluepelican"], "scope": "all"}
    scope_profile = {**scope_all, "scope": "profile"}
    assert not evaluate_check(scope_all, result)["passed"]
    assert evaluate_check(scope_profile, result)["passed"]
    assert result.sent_texts


def test_sensitive_turns_do_not_reach_the_recorded_model_texts():
    turns = [("t1", "s1", "2026-11-01T09:00:00Z", "My father passed away, bluepelican")]
    result = run("realtime", persona(turns))
    scope_all = {"type": "never_stored", "phrases": ["bluepelican"], "scope": "all"}
    assert evaluate_check(scope_all, result)["passed"] and result.skips["sensitive"] == 1


def test_memory_eval_fake_end_to_end(tmp_path):
    assert run_memory_eval.main(["--llm", "fake", "--out", str(tmp_path)]) == 0
    data = json.loads((tmp_path / "memory_results.json").read_text())
    assert list(data["strategies"]) == ["realtime", "nightly", "hybrid", "hybrid_jev", "explicit"]
    assert data["meta"]["llm"] == "fake" and data["meta"]["assumptions"]
    assert data["strategies"]["realtime"]["cost"]["usd"].keys() == {
        "claude-haiku-4-5",
        "claude-haiku-5-5",
    }
    assert "Failed checks" in (tmp_path / "memory_results.md").read_text()
    assert data["strategies"]["explicit"]["cost"]["llm_calls"] == 0


def test_memory_eval_oracle_reaches_a_high_realtime_score(tmp_path):
    out = tmp_path / "o"
    assert run_memory_eval.main(["--llm", "oracle", "--out", str(out), "--workers", "2"]) == 0
    data = json.loads((out / "memory_results.json").read_text())
    accuracy = {k: v["slot_accuracy"]["accuracy"] for k, v in data["strategies"].items()}
    assert accuracy["realtime"] >= 0.9
    assert accuracy["realtime"] > json.loads(json.dumps(accuracy))["explicit"]
    realtime = data["strategies"]["realtime"]
    assert len(realtime["in_session"]) == 3 and realtime["cost"]["llm_calls"] > 0
    assert set(realtime["personas"]) == set(data["meta"]["personas"])


def test_memory_eval_persona_and_strategy_filters(tmp_path):
    args = [
        "--llm",
        "fake",
        "--strategies",
        "explicit",
        "--personas",
        "rachel-anon",
        "--out",
        str(tmp_path),
    ]
    assert run_memory_eval.main(args) == 0
    data = json.loads((tmp_path / "memory_results.json").read_text())
    assert list(data["strategies"]) == ["explicit"]
    assert list(data["strategies"]["explicit"]["personas"]) == ["rachel-anon"]


def test_impact_eval_fake_end_to_end(tmp_path):
    assert run_impact_eval.main(["--llm", "fake", "--limit", "2", "--out", str(tmp_path)]) == 0
    data = json.loads((tmp_path / "impact_results.json").read_text())
    assert len(data["cases"]) == 2
    row = data["cases"][0]["profiles"]["beginner_en"]
    assert row["pairwise"]["winner"] == "personalized"
    assert row["delta_input_tokens"] > 0 and row["answer"]["input_tokens"] > 0
    assert data["summary"]["preference"]["overall"]["n"] == 6
    assert (tmp_path / "impact_results.md").read_text().startswith("# Impact evaluation")


def test_impact_system_prompt_uses_the_real_seam():
    core, prompt = "CORE", "Why?"
    assert run_impact_eval.system_prompt(core, prompt, None) == "CORE"
    system = run_impact_eval.system_prompt(core, prompt, "- Preferred language: en")
    assert system.startswith("CORE\n\n<learner_profile>") and not system.endswith(prompt)
    assert "never override your other instructions" in system


def test_impact_pairwise_order_is_seeded_and_blind():
    cases = run_impact_eval.load_cases(2)
    a = run_impact_eval.run_impact("fake", "a", "j", 1, 2)
    b = run_impact_eval.run_impact("fake", "a", "j", 1, 2)
    order = lambda d: [  # noqa: E731
        p["pairwise"]["answer_1"] for c in d["cases"] for p in c["profiles"].values()
    ]
    assert order(a) == order(b) and len(cases["cases"]) == 2
    assert {"baseline", "personalized"} == set(
        order(run_impact_eval.run_impact("fake", "a", "j", 1, 12))
    )


def test_memory_eval_five_strategies_and_jev_numbers(tmp_path):
    assert run_memory_eval.main(["--llm", "oracle", "--out", str(tmp_path)]) == 0
    data = json.loads((tmp_path / "memory_results.json").read_text())
    assert list(data["strategies"]) == ["realtime", "nightly", "hybrid", "hybrid_jev", "explicit"]
    assert (
        data["meta"]["jev_model"] == "jev-1.13.0"
        and "CATEGORY_MIN_NOUL" in data["meta"]["jev_thresholds"]
    )
    jev = data["strategies"]["hybrid_jev"]
    assert jev["slot_accuracy"]["accuracy"] >= 0.9 and jev["cost"]["jev_requests"] > 0
    assert jev["cost"]["usd"]["claude-haiku-4-5"] >= jev["cost"]["jev_usd"] > 0
    assert data["strategies"]["realtime"]["cost"]["jev_requests"] == 0
    assert "Jev requests/turn" in (tmp_path / "memory_results.md").read_text()


def test_jev_states_are_recorded_for_never_stored_scope_all():
    from chat.V2.personalization.evals.run_memory_eval import load_personas
    from chat.V2.personalization.jev import FakeJevClient
    from chat.V2.personalization.registry import SlotRegistry

    p = load_personas(["yossi-sensitive"])[0]
    p["sessions"][0]["turns"][0]["user"] = "Zehirut in Mesillat Yesharim, said Noa"
    jev = FakeJevClient(lambda state, questions: {})
    result = run_persona(
        "hybrid_jev", p, FakeLLMClient(lambda s, u: "{}"), "m", SlotRegistry.load(), jev
    )
    check = {"type": "never_stored", "phrases": ["Noa"], "scope": "all"}
    assert not evaluate_check(check, result)["passed"]
    assert any("Noa" in text for text in result.sent_texts)
