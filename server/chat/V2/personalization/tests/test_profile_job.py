"""The Profile job end to end with a fake model and a fake Jev. Made-up text only."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection

from chat.models import PersonalizationSettings, ProfileWriterRun
from chat.V2.personalization import profile_store as store
from chat.V2.personalization import quota
from chat.V2.personalization.jev import FakeJevClient, JevAnswer
from chat.V2.personalization.llm import FakeLLMClient
from chat.V2.personalization.profile_job import run_profile_job
from chat.V2.personalization.profile_store import SectionData
from chat.V2.prompts.chapters import load_chapter

from .test_profile_store import add_prompt

pytestmark = pytest.mark.django_db

USER = "user-1"
T0 = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
EXTRACTION = load_chapter("profile_extraction")


@pytest.fixture(autouse=True)
def env(monkeypatch, settings):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-not-real")
    settings.PERSONALIZATION_JEV_GATE = "off"


def item(key, heading, action="add", origin="inferred", sessions=2, notes=("a note",)):
    return {
        "key": key,
        "heading": heading,
        "action": action,
        "origin": origin,
        "notes": list(notes),
        "sessions": sessions,
    }


def model(extraction=None, bodies=None, check="no"):
    """A fake model: extraction JSON, writer JSON (a body per requested heading), check answer."""

    def respond(system, user):
        if system == EXTRACTION:
            return json.dumps({"sections": extraction or []})
        if "yes or no" in system:
            return check
        wanted = [
            line[3:].strip() for line in user.splitlines() if line.startswith("## ")
        ]  # headings the writer was asked for
        return json.dumps(
            {
                "sections": [
                    {"heading": h, "body": (bodies or {}).get(h, f"Body of {h}.")} for h in wanted
                ]
            }
        )

    return FakeLLMClient(respond)


def seed(*prompts, at=T0):
    for i, (text, session) in enumerate(prompts):
        add_prompt(USER, text, session=session, at=at + timedelta(minutes=i))


def two_sessions():
    seed(("show me the commentary on the first verse", "s1"), ("short answers please", "s2"))


def section(heading, body, *, key=None, kind="core", origin="inferred", at=None, position=0):
    return SectionData(key, kind, heading, body, origin, at or NOW, position)


def put(*sections):
    rev = store.get_settings(USER).profile_revision
    assert store.replace_sections(
        USER, rev, list(sections), updated_by="la", processed_through=None, set_notice=False
    )


def run(llm, jev=None, **kw):
    return run_profile_job(USER, kw.pop("trigger", "idle"), now=NOW, llm=llm, jev=jev, **kw)


def jev_answering(default=0.9, **by_key):
    return FakeJevClient(
        lambda state, qs: {k: JevAnswer("noul", noul=by_key.get(k, default)) for k in qs}
    )


def watermark():
    return store.get_settings(USER).processed_through


def test_no_new_prompts_is_no_change_without_a_model_call():
    llm = model()
    result = run(llm)
    assert result.status == "no_change" and llm.calls == []
    assert ProfileWriterRun.objects.get(pk=result.run_id).status == "no_change"


def test_applied_end_to_end():
    two_sessions()
    llm = model([item("answer_style", "Kind of answers you want", sessions=1)])
    result = run(llm)
    assert result.status == "applied"
    sections = store.get_sections(USER)
    assert [(s.key, s.heading, s.kind) for s in sections] == [
        ("answer_style", "Kind of answers you want", "core")
    ]
    settings_ = store.get_settings(USER)
    assert settings_.profile_updated_by == "la" and settings_.notice_pending
    assert settings_.profile_revision == 1
    assert watermark() == T0 + timedelta(minutes=1)
    row = ProfileWriterRun.objects.get(pk=result.run_id)
    assert row.status == "applied" and len(row.prompt_ids) == 2 and row.model
    assert "profile_extraction" in row.chapter_hashes and row.profile_after


def test_sensitive_prompt_is_dropped_before_any_call():
    seed(("my mother's illness is hard, explain the verse", "s1"))
    llm, jev = model(), jev_answering()
    settings_run = run(llm, jev)
    assert settings_run.status == "no_change"
    assert llm.calls == [] and jev.calls == []
    assert watermark() == T0  # the dropped prompt is still passed


def test_sensitive_text_never_reaches_jev_or_the_model(settings):
    settings.PERSONALIZATION_JEV_GATE = "on"
    seed(("my mother's illness is hard", "s1"), ("short answers please", "s2"))
    llm, jev = (
        model([item("answer_style", "Kind of answers you want")]),
        jev_answering(0.9, sensitive_0=0.1),
    )
    assert run(llm, jev).status == "applied"
    sent = json.dumps(jev.calls[0][0]) + " ".join(c["user"] for c in llm.calls)
    assert "illness" not in sent and "short answers" in sent


def test_gate_says_not_relevant_ends_the_run(settings):
    settings.PERSONALIZATION_JEV_GATE = "on"
    seed(("what is the weather", "s1"))
    llm, jev = model(), jev_answering(0.1)
    result = run(llm, jev)
    assert result.status == "no_change" and llm.calls == []
    assert len(jev.calls) == 1 and watermark() == T0


def test_only_gated_slots_reach_extraction(settings):
    settings.PERSONALIZATION_JEV_GATE = "on"
    two_sessions()
    jev = jev_answering(0.1, relevant=0.9, slot_answer_style=0.9)
    llm = model(
        [
            item("answer_style", "Kind of answers you want"),
            item("commentators", "Commentators you seek out"),
        ]
    )
    result = run(llm, jev)
    extraction_user = next(c["user"] for c in llm.calls if c["system"] == EXTRACTION)
    assert "answer_style" in extraction_user and "commentators" not in extraction_user
    assert result.status == "applied"
    assert [s.key for s in store.get_sections(USER)] == ["answer_style"]


def test_confirm_refreshes_last_supported_at_only():
    old = NOW - timedelta(days=30)
    put(section("Reply language", "You write in English.", key="reply_language", at=old))
    two_sessions()
    llm = model([item("reply_language", "Reply language", action="confirm")])
    result = run(llm)
    (s,) = store.get_sections(USER)
    assert s.body == "You write in English." and s.last_supported_at == T0 + timedelta(minutes=1)
    assert [
        c for c in llm.calls if "yes or no" not in c["system"] and c["system"] != EXTRACTION
    ] == []
    assert result.status == "applied"
    assert not store.get_settings(USER).notice_pending  # nothing the user would read changed


def test_inferred_update_does_not_replace_stated():
    put(section("Reply language", "You asked for Hebrew.", key="reply_language", origin="stated"))
    two_sessions()
    llm = model(
        [item("reply_language", "Reply language", action="update", origin="inferred")],
        {"Reply language": "You write in English."},
    )
    run(llm)
    (s,) = store.get_sections(USER)
    assert s.body == "You asked for Hebrew." and s.origin == "stated"


def test_stated_update_replaces_stated():
    put(section("Reply language", "You asked for Hebrew.", key="reply_language", origin="stated"))
    two_sessions()
    llm = model(
        [item("reply_language", "Reply language", action="update", origin="stated")],
        {"Reply language": "You now ask for English."},
    )
    run(llm)
    (s,) = store.get_sections(USER)
    assert s.body == "You now ask for English."


def test_dynamic_section_needs_two_sessions():
    two_sessions()
    run(model([item(None, "Teaching", sessions=1)]))
    assert store.get_sections(USER) == []

    PersonalizationSettings.objects.filter(user_id=USER).update(processed_through=None)
    run(model([item(None, "Teaching", sessions=2)]))
    (s,) = store.get_sections(USER)
    assert (s.kind, s.key, s.heading) == ("dynamic", None, "Teaching")


def test_dynamic_duplicate_of_a_core_heading_is_refused():
    two_sessions()
    run(model([item(None, " current INTERESTS ", sessions=2)]))
    assert store.get_sections(USER) == []


def test_sections_older_than_60_days_are_dropped():
    put(
        section(
            "Reply language",
            "You write in English.",
            key="reply_language",
            at=NOW - timedelta(days=61),
        ),
        section(
            "Current interests",
            "Rashi.",
            key="current_interests",
            at=NOW - timedelta(days=5),
            position=1,
        ),
    )
    two_sessions()
    result = run(model([item("answer_style", "Kind of answers you want")]))
    assert result.status == "applied"
    assert [s.key for s in store.get_sections(USER)] == ["current_interests", "answer_style"]


def test_failed_check_rejects_and_changes_nothing():
    put(section("Reply language", "You write in English.", key="reply_language"))
    before = store.get_settings(USER)
    two_sessions()
    llm = model(
        [item("answer_style", "Kind of answers you want")],
        {"Kind of answers you want": "You like " + "long " * 160},
    )
    result = run(llm)
    assert result.status == "rejected" and result.reason == "words"
    after = store.get_settings(USER)
    assert after.profile_revision == before.profile_revision and after.processed_through is None
    assert [s.key for s in store.get_sections(USER)] == ["reply_language"]
    row = ProfileWriterRun.objects.get(pk=result.run_id)
    assert row.status == "rejected" and row.failed_check == "words"


def test_model_check_yes_rejects():
    two_sessions()
    result = run(model([item("answer_style", "Kind of answers you want")], check="yes"))
    assert result.status == "rejected" and result.reason == "never_store_model"
    assert store.get_sections(USER) == []


def test_second_run_with_nothing_new_is_no_change():
    two_sessions()
    assert run(model([item("answer_style", "Kind of answers you want")])).status == "applied"
    llm = model()
    assert run(llm).status == "no_change" and llm.calls == []


def test_a_prompt_saved_during_the_run_is_left_for_the_next_run():
    two_sessions()
    late = T0 + timedelta(minutes=5)

    def respond_and_save(system, user):
        if system == EXTRACTION:
            add_prompt(USER, "a prompt that arrived mid-run", session="s3", at=late)
        return json.dumps({"sections": []})

    run(FakeLLMClient(respond_and_save))
    rows = store.unprocessed_user_prompts(USER, watermark())
    assert [r.text for r in rows] == ["a prompt that arrived mid-run"]


def test_a_held_lock_is_busy():
    two_sessions()
    lease = quota.acquire_job_lock(USER, NOW)
    assert lease
    llm = model()
    result = run(llm)
    assert result.status == "busy" and llm.calls == []
    quota.release_job_lock(USER, lease)
    assert run(model()).status != "busy"


def test_the_lock_is_released_after_a_run():
    two_sessions()
    run(model())
    assert quota.acquire_job_lock(USER, NOW)


def test_over_quota_makes_no_call(monkeypatch):
    monkeypatch.setenv("PERSONALIZATION_JOB_PER_HOUR", "0")
    two_sessions()
    llm = model()
    result = run(llm)
    assert result.status == "quota" and llm.calls == []
    assert watermark() is None


def boom(system, user):
    raise TimeoutError("slow")


def test_a_model_timeout_fails_and_the_third_failure_parks():
    two_sessions()
    for _ in range(3):
        result = run(FakeLLMClient(boom))
        assert result.status == "failed"
    assert watermark() is None
    llm = model()
    parked = run(llm)
    assert parked.status == "failed" and parked.reason == "parked" and llm.calls == []
    # An explicit run now lifts the park for that attempt.
    assert (
        run(
            model(
                [item("answer_style", "Kind of answers you want")],
            ),
            trigger="run_now",
        ).status
        == "applied"
    )


def test_a_success_between_failures_resets_the_count():
    two_sessions()
    run(FakeLLMClient(boom))
    run(FakeLLMClient(boom))
    assert run(model([item("answer_style", "Kind of answers you want")])).status == "applied"
    seed(("another prompt", "s3"), at=T0 + timedelta(hours=1))
    assert run(FakeLLMClient(boom)).status == "failed"
    assert run(FakeLLMClient(boom)).reason != "parked"


def test_a_malformed_extraction_fails():
    two_sessions()
    result = run(FakeLLMClient(lambda s, u: "not json"))
    assert result.status == "failed" and watermark() is None


def test_jev_down_still_runs_the_model_path(settings):
    settings.PERSONALIZATION_JEV_GATE = "on"
    two_sessions()

    def down(state, qs):
        raise TimeoutError()

    result = run(model([item("answer_style", "Kind of answers you want")]), FakeJevClient(down))
    assert result.status == "applied"


def test_the_jev_gate_is_not_used_without_a_key(settings, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    settings.PERSONALIZATION_JEV_GATE = "on"
    two_sessions()
    jev = jev_answering(0.0)
    assert run(model([item("answer_style", "Kind of answers you want")]), jev).status == "applied"
    assert jev.calls == []


@pytest.mark.django_db(transaction=True)
def test_model_calls_happen_outside_a_transaction():
    two_sessions()
    seen = []

    def respond(system, user):
        seen.append(connection.in_atomic_block)
        if system == EXTRACTION:
            return json.dumps({"sections": [item("answer_style", "Kind of answers you want")]})
        if "yes or no" in system:
            return "no"
        return json.dumps({"sections": [{"heading": "Kind of answers you want", "body": "Short."}]})

    assert run(FakeLLMClient(respond)).status == "applied"
    assert len(seen) == 3 and not any(seen)


def test_a_parked_user_is_retried_when_a_new_prompt_arrives():
    two_sessions()
    for _ in range(3):
        assert run(FakeLLMClient(boom)).status == "failed"
    assert run(model()).reason == "parked"
    seed(("one more prompt", "s3"), at=NOW + timedelta(hours=1))
    llm = model([item("answer_style", "Kind of answers you want", sessions=1)])
    result = run(llm)
    assert result.reason != "parked" and result.status == "applied" and llm.calls


def test_an_unknown_window_is_refused():
    with pytest.raises(ValueError):
        run(model(), window="weekly")


def _only_run():
    return ProfileWriterRun.objects.get(user_id=USER)


def test_the_extraction_is_a_summary_with_no_notes_or_headings():
    two_sessions()
    llm = model(
        [
            item(
                "answer_style",
                "Kind of answers you want",
                notes=("zebra-note-text", "lives in Haifa"),
            ),
            item(None, "Based in Haifa", action="none", notes=("fine note",)),
        ]
    )
    run(llm)
    text = _only_run().extraction
    for fragment in ("zebra-note-text", "Haifa", "fine note", "Based in"):
        assert fragment not in text
    assert json.loads(text)["sections"][0] == {
        "key": "answer_style",
        "action": "add",
        "origin": "inferred",
        "sessions": 2,
    }


def test_a_run_finishing_after_a_clear_records_no_pre_clear_text():
    two_sessions()
    put(section("Kind of answers you want", "You like long answers.", key="answer_style"))
    base = model([item("answer_style", "Kind of answers you want", notes=("zebra-note-text",))])

    def respond(system, user):
        if system == EXTRACTION:
            store.clear_profile(USER)  # the user clears while the job is in flight
        return base.responder(system, user)

    result = run(FakeLLMClient(respond))
    row = ProfileWriterRun.objects.get(pk=result.run_id)
    assert row.status == "failed" and row.reason == "revision_moved"
    assert row.extraction == "" and row.profile_before == "" and row.profile_after == ""
    assert not store.get_sections(USER)


def test_unfinished_turns_are_not_read_and_the_batch_stops_before_them():
    from django.utils import timezone

    now = timezone.now()
    add_prompt(
        USER, "done", session="s1", at=now - timedelta(minutes=5), processing_state="completed"
    )
    add_prompt(
        USER, "running", session="s2", at=now - timedelta(minutes=4), processing_state="running"
    )
    add_prompt(
        USER,
        "later done",
        session="s3",
        at=now - timedelta(minutes=3),
        processing_state="completed",
    )
    got = [p.text for p in store.unprocessed_user_prompts(USER, None)]
    assert got == ["done"]  # never past the unfinished turn, so the watermark cannot pass it


def test_a_stale_unfinished_turn_is_abandoned_and_does_not_block():
    from django.utils import timezone

    now = timezone.now()
    add_prompt(USER, "stuck", session="s1", at=now - timedelta(hours=2), processing_state="started")
    add_prompt(
        USER, "done", session="s2", at=now - timedelta(minutes=3), processing_state="completed"
    )
    assert [p.text for p in store.unprocessed_user_prompts(USER, None)] == ["done"]


def test_a_writer_that_omits_or_mangles_a_requested_section_fails_the_run():
    bad_replies = [
        [],
        [{"heading": "Some other heading", "body": "Text."}],
        [{"heading": "Kind of answers you want", "body": 5}],
        [{"heading": "Kind of answers you want", "body": "  "}],
    ]
    for reply in bad_replies:
        ProfileWriterRun.objects.all().delete()
        PersonalizationSettings.objects.all().delete()
        from chat.models import ChatMessage

        ChatMessage.objects.all().delete()
        two_sessions()

        def respond(system, user, reply=reply):
            if system == EXTRACTION:
                return json.dumps(
                    {"sections": [item("answer_style", "Kind of answers you want", sessions=1)]}
                )
            return json.dumps({"sections": reply})

        result = run(FakeLLMClient(respond))
        assert result.status == "failed" and result.reason == "bad_writer_output", reply
        assert store.get_sections(USER) == []
        assert watermark() is None or watermark() < T0


def test_a_recent_blank_state_turn_without_a_response_is_unfinished():
    """The chat pipeline saves the user message with a blank state and marks it STARTED a few
    queries later; the job must not read it in between, nor move the watermark past it."""
    from django.utils import timezone

    now = timezone.now()
    add_prompt(
        USER, "done", session="s1", at=now - timedelta(minutes=5), processing_state="completed"
    )
    blank = add_prompt(
        USER, "just saved", session="s2", at=now - timedelta(minutes=2), processing_state=""
    )
    add_prompt(
        USER, "later", session="s3", at=now - timedelta(minutes=1), processing_state="completed"
    )
    assert [p.text for p in store.unprocessed_user_prompts(USER, None)] == ["done"]
    two = run_profile_job(USER, "idle", now=now, llm=model())
    assert two.status in ("no_change", "applied")
    assert watermark() is None or watermark() < blank.server_timestamp


def test_a_blank_state_turn_counts_as_finished_when_old_or_answered():
    from django.utils import timezone

    from chat.models import ChatMessage

    now = timezone.now()
    add_prompt(USER, "old blank", session="s1", at=now - timedelta(hours=2), processing_state="")
    answered = add_prompt(
        USER,
        "answered blank",
        session="s2",
        at=now - timedelta(minutes=2),
        turn_id="turn-a",
        processing_state="",
    )
    ChatMessage.objects.create(
        message_id="a1",
        session_id="s2",
        user_id=USER,
        turn_id="turn-a",
        role=ChatMessage.Role.ASSISTANT,
        content="reply",
        status=ChatMessage.Status.SUCCESS,
    )
    add_prompt(
        USER, "unanswered blank", session="s3", at=now - timedelta(minutes=1), processing_state=""
    )
    got = [p.text for p in store.unprocessed_user_prompts(USER, None)]
    assert got == ["old blank", "answered blank"] and answered


def test_a_new_section_is_written_from_an_empty_current_body():
    two_sessions()
    llm = model([item("answer_style", "Kind of answers you want", sessions=1)])
    assert run(llm).status == "applied"
    writer = next(c for c in llm.calls if "## Output for this job" in c["system"])
    assert "(new section)" not in writer["user"]
    assert "Current: \n" in writer["user"]
    assert "new section has no current text" in writer["system"]


@pytest.mark.parametrize("echo", ["(new section)", "(New Section)", " (new section). "])
def test_a_writer_echoing_the_new_section_placeholder_is_a_bad_output(echo):
    two_sessions()
    llm = model(
        [item("answer_style", "Kind of answers you want", sessions=1)],
        {"Kind of answers you want": echo},
    )
    result = run(llm)
    assert result.status == "failed" and result.reason == "bad_writer_output"
    assert store.get_sections(USER) == []
