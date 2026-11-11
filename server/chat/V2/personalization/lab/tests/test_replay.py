"""The simulated clock, auto-accept, recording client and the end-to-end evaluators."""

from datetime import UTC, datetime

from chat.V2.personalization.lab.replay import (
    RecordingLLMClient,
    StrategyPipeline,
    job_time,
    night_key,
    oracle_responder,
    replay_persona,
)
from chat.V2.personalization.lab.scoring import evaluate_check
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


def run(strategy, p, responder=None, jev=None):
    llm = FakeLLMClient(responder or oracle_responder(p))
    pipeline = StrategyPipeline(strategy, llm, "claude-haiku-4-5", jev=jev)
    return replay_persona(pipeline, p)


def test_batch_runs_once_per_day_boundary_and_once_at_the_end():
    result = run("nightly", persona(TURNS))
    assert result.jobs == [
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
    assert {c["step"] for c in result.calls} == {"extract_turn"}


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
    assert explicit.skips["explicit_only"] == 3 and explicit.calls == []


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


def test_jev_states_are_recorded_for_never_stored_scope_all():
    from chat.V2.personalization.jev import FakeJevClient
    from chat.V2.personalization.lab.datasets import load_dataset

    p = next(x for x in load_dataset("syn1").items if x["id"] == "yossi-sensitive")
    p["sessions"][0]["turns"][0]["user"] = "Zehirut in Mesillat Yesharim, said Noa"
    jev = FakeJevClient(lambda state, questions: {})
    result = run("hybrid_jev", p, responder=lambda s, u: "{}", jev=jev)
    check = {"type": "never_stored", "phrases": ["Noa"], "scope": "all"}
    assert not evaluate_check(check, result)["passed"]
    assert any("Noa" in text for text in result.sent_texts)


def test_clock_small_hours_turn_goes_to_previous_night():
    late = datetime(2026, 11, 1, 22, 30, tzinfo=UTC)
    small = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)
    after_job = datetime(2026, 11, 2, 3, 0, tzinfo=UTC)
    assert night_key(small) == night_key(late) and night_key(after_job) != night_key(late)
    assert job_time(late) == job_time(small) == datetime(2026, 11, 2, 3, tzinfo=UTC)
    turns = [
        ("t1", "s1", "2026-11-01T22:30:00Z", "Berakhot 2a please"),
        ("t2", "s1", "2026-11-02T01:00:00Z", "and Berakhot 2b"),  # same night: no job between
        ("t3", "s2", "2026-11-02T09:00:00Z", "and Berakhot 3a"),
    ]
    result = run("nightly", persona(turns))
    assert [s["kind"] for s in result.snapshots] == ["turn", "turn", "batch", "turn", "batch"]
    assert result.jobs[0] == datetime(2026, 11, 2, 3, tzinfo=UTC)


def test_a_snapshot_is_recorded_after_every_turn():
    result = run("realtime", persona(TURNS))
    assert [s["turn_id"] for s in result.turn_snapshots()] == ["t1", "t2", "t3"]
