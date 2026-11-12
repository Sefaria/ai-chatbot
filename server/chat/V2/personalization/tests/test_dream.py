"""The dream: rebuild the Profile from the last 60 days into a candidate, swap if still current.
Fake model, fake Jev, made-up text only."""

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
NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
DREAM = load_chapter("profile_dream")


@pytest.fixture(autouse=True)
def env(monkeypatch, settings):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-not-real")
    settings.PERSONALIZATION_JEV_GATE = "off"


def entry(key, heading, body, support, origin="inferred"):
    return {"key": key, "heading": heading, "body": body, "origin": origin, "support": support}


def dream_model(sections, check="no", on_dream=None):
    def respond(system, user):
        if system == DREAM:
            if on_dream:
                on_dream(user)
            return json.dumps({"sections": sections})
        assert "yes or no" in system
        return check

    return FakeLLMClient(respond)


def prompt(text, session, days_ago=1.0, minutes=0):
    return add_prompt(
        USER, text, session=session, at=NOW - timedelta(days=days_ago, minutes=-minutes)
    )


def sec(heading, body, *, key=None, kind="core", origin="inferred", at=None, position=0):
    return SectionData(key, kind, heading, body, origin, at or NOW - timedelta(days=2), position)


def put(*sections, notice=False):
    rev = store.get_settings(USER).profile_revision
    assert store.replace_sections(
        USER, rev, list(sections), updated_by="la", processed_through=None, set_notice=notice
    )


def dream(llm, jev=None, **kw):
    return run_profile_job(USER, "dream", window="dream", now=NOW, llm=llm, jev=jev, **kw)


def rows():
    return [(s.heading, s.body, s.origin) for s in store.get_sections(USER)]


def settings_row():
    return store.get_settings(USER)


def test_reads_the_last_60_days_and_nothing_older():
    prompt("ancient question about the zebra", "s0", days_ago=61)
    prompt("recent question about the otter", "s1", days_ago=3)
    llm = dream_model([entry("answer_style", "x", "You like short answers.", [1])])
    assert dream(llm).status == "applied"
    sent = llm.calls[0]["user"]
    assert "otter" in sent and "zebra" not in sent
    row = ProfileWriterRun.objects.get(user_id=USER)
    assert row.window == "dream" and len(row.prompt_ids) == 1 and row.watermark_from is None
    assert "profile_dream" in row.chapter_hashes


def test_merges_two_overlapping_sections_into_one():
    prompt("tell me about the otter commentary", "s1", days_ago=5)
    prompt("more otter commentary please", "s2", days_ago=4)
    put(
        sec("Commentators you seek out", "You ask for Rashi.", key="commentators"),
        sec("Commentary habits", "You ask for Rashi often.", kind="dynamic", position=1),
    )
    llm = dream_model(
        [entry("commentators", "", "Since 8 November 2026 you ask for Rashi and others.", [1, 2])]
    )
    assert dream(llm).status == "applied"
    assert rows() == [
        (
            "Commentators you seek out",
            "Since 8 November 2026 you ask for Rashi and others.",
            "inferred",
        )
    ]
    assert settings_row().profile_updated_by == "la" and settings_row().notice_pending


def test_drops_a_section_with_no_supporting_prompt_in_the_window():
    prompt("a recent question", "s1")
    put(
        sec("Learning rhythm", "You study a page a day.", key="learning_rhythm"),
        sec("Current interests", "You study the otter.", key="current_interests", position=1),
    )
    llm = dream_model([entry("current_interests", "", "You study the otter.", [1])])
    assert dream(llm).status == "applied"
    assert [h for h, _, _ in rows()] == ["Current interests"]


def test_a_section_with_no_valid_support_rejects_the_dream():
    prompt("a recent question", "s1")
    put(sec("Learning rhythm", "You study a page a day.", key="learning_rhythm"))
    for support in ([], [7], ["x"]):
        llm = dream_model([entry("answer_style", "", "You like short answers.", support)])
        result = dream(llm)
        assert result.status == "rejected" and result.reason == "unsupported_section"
    assert [h for h, _, _ in rows()] == ["Learning rhythm"]
    assert settings_row().profile_revision == 1


def test_duplicate_headings_reject_the_dream():
    prompt("one", "s1")
    prompt("two", "s2")
    llm = dream_model(
        [
            entry(None, "Teaching", "You teach a class.", [1, 2]),
            entry(None, "teaching", "You teach on Fridays.", [1, 2]),
        ]
    )
    result = dream(llm)
    assert result.status == "rejected" and result.reason == "duplicate_heading"
    assert rows() == []


def test_a_dynamic_section_needs_two_sessions_and_four_is_the_cap():
    prompt("one", "s1")
    prompt("two", "s1")
    prompt("three", "s2")
    llm = dream_model(
        [
            entry(None, "One session only", "You did one thing.", [1, 2]),
            entry(None, "Two sessions", "You did another thing.", [1, 3]),
        ]
    )
    assert dream(llm).status == "applied"
    assert [h for h, _, _ in rows()] == ["Two sessions"]
    llm = dream_model([entry(None, f"Topic {i}", f"Body {i}.", [1, 3]) for i in range(5)])
    result = dream(llm)
    assert result.status == "rejected" and result.reason == "dynamic_cap"


def test_an_unchanged_candidate_writes_nothing_and_leaves_the_notice_alone():
    prompt("a recent question", "s1")
    put(sec("Kind of answers you want", "You like short answers.", key="answer_style"))
    before = settings_row()
    llm = dream_model([entry("answer_style", "", "You like short answers.", [1])])
    result = dream(llm)
    assert result.status == "no_change" and result.reason == "dream_unchanged"
    after = settings_row()
    assert after.profile_revision == before.profile_revision
    assert after.profile_updated_at == before.profile_updated_at
    assert after.notice_pending is False
    put(sec("Kind of answers you want", "You like short answers.", key="answer_style"), notice=True)
    assert dream(llm).status == "no_change"
    assert settings_row().notice_pending is True  # unchanged: still pending


def test_a_clear_between_start_and_swap_discards_the_candidate():
    prompt("a recent question", "s1")
    put(sec("Learning rhythm", "You study a page a day.", key="learning_rhythm"))
    revision = settings_row().profile_revision

    def clear_now(_user):
        store.clear_profile(USER)  # the user presses Clear while the dream is consolidating

    llm = dream_model(
        [entry("answer_style", "", "You like short answers.", [1])], on_dream=clear_now
    )
    result = dream(llm)
    assert result.status == "failed" and result.reason == "revision_moved"
    assert rows() == []  # the candidate never came back
    after = settings_row()
    assert after.profile_revision == revision + 1 and after.cleared_at is not None
    assert after.profile_updated_by is None and after.notice_pending is False
    run = ProfileWriterRun.objects.get(pk=result.run_id)
    assert run.status == "failed" and run.profile_after == ""


def test_a_dream_edits_nothing_before_the_swap():
    prompt("a recent question", "s1")
    put(sec("Learning rhythm", "You study a page a day.", key="learning_rhythm"))
    before = (rows(), settings_row())
    seen = []

    def look(_user):
        seen.append((rows(), settings_row()))

    llm = dream_model([entry("answer_style", "", "You like short answers.", [1])], on_dream=look)
    assert dream(llm).status == "applied"
    assert seen == [before]


def test_a_section_the_user_asked_for_in_the_settings_box_survives_the_dream():
    prompt("a recent question", "s1")
    put(
        sec(
            "Current interests",
            "Only in the context of parsha.",
            key="current_interests",
            origin="stated",
            at=NOW - timedelta(days=1),
        )
    )
    llm = dream_model([entry("answer_style", "", "You like short answers.", [1])])
    assert dream(llm).status == "applied"
    assert sorted(h for h, _, _ in rows()) == ["Current interests", "Kind of answers you want"]


def test_stated_beats_a_new_inferred_value():
    prompt("a recent question", "s1")
    put(
        sec(
            "Kind of answers you want",
            "You want sources first.",
            key="answer_style",
            origin="stated",
            at=NOW - timedelta(days=1),
        )
    )
    llm = dream_model([entry("answer_style", "", "You like long answers.", [1], origin="inferred")])
    assert dream(llm).status == "no_change"
    assert rows() == [("Kind of answers you want", "You want sources first.", "stated")]


def test_sensitive_and_hidden_prompts_never_reach_the_model():
    from chat.models import ChatSession

    prompt("my mother's illness is hard, explain the verse", "s1")
    prompt("visible otter question", "s2")
    prompt("hidden zebra question", "hid")
    ChatSession.objects.create(session_id="hid", user_id=USER, is_deleted=True)
    llm = dream_model([entry("answer_style", "", "You like short answers.", [1])])
    assert dream(llm).status == "applied"
    sent = llm.calls[0]["user"]
    assert "otter" in sent and "illness" not in sent and "zebra" not in sent


def test_the_jev_sensitive_screen_applies_to_the_window(settings):
    settings.PERSONALIZATION_JEV_GATE = "on"
    prompt("first harmless question", "s1")
    prompt("second quiet question", "s2")

    jev = FakeJevClient(
        lambda state, qs: {
            k: JevAnswer("noul", noul=0.95 if k == "sensitive_1" else 0.05) for k in qs
        }
    )
    llm = dream_model([entry("answer_style", "", "You like short answers.", [1])])
    assert dream(llm, jev).status == "applied"
    sent = llm.calls[0]["user"]
    assert "first harmless" in sent and "second quiet" not in sent


def test_no_prompts_and_no_profile_is_no_change_without_a_model_call():
    llm = dream_model([])
    result = dream(llm)
    assert result.status == "no_change" and llm.calls == []


def test_an_empty_window_clears_what_it_no_longer_supports():
    put(sec("Learning rhythm", "You study a page a day.", key="learning_rhythm"))
    llm = dream_model([])
    assert dream(llm).status == "applied"
    assert llm.calls == [] and rows() == []


def test_a_cleared_profile_is_not_rebuilt_from_older_prompts():
    prompt("question before the clear", "s1", days_ago=2)
    store.clear_profile(USER)
    llm = dream_model([])
    assert dream(llm).status == "no_change" and llm.calls == []


def test_the_watermark_moves_to_the_newest_prompt_read():
    prompt("a recent question", "s1", days_ago=3)
    prompt("a newer question", "s2", days_ago=1)
    llm = dream_model([entry("answer_style", "", "You like short answers.", [1])])
    dream(llm)
    assert settings_row().processed_through == NOW - timedelta(days=1)


def test_a_bad_model_reply_fails_and_changes_nothing():
    prompt("a recent question", "s1")
    put(sec("Learning rhythm", "You study a page a day.", key="learning_rhythm"))
    result = dream(FakeLLMClient(lambda s, u: "not json"))
    assert result.status == "failed" and rows()[0][0] == "Learning rhythm"
    assert settings_row().profile_revision == 1


def test_a_failed_check_rejects_and_changes_nothing():
    prompt("a recent question", "s1")
    put(sec("Learning rhythm", "You study a page a day.", key="learning_rhythm"))
    result = dream(dream_model([entry("answer_style", "", "You are based in Paris.", [1])]))
    assert result.status == "rejected" and result.reason == "never_store_terms"
    assert rows()[0][0] == "Learning rhythm"
    result = dream(dream_model([entry("answer_style", "", "You like quiet.", [1])], check="yes"))
    assert result.reason == "never_store_model"


def test_over_the_word_cap_rejects():
    prompt("a recent question", "s1")
    body = " ".join(["word"] * 151)
    result = dream(dream_model([entry("answer_style", "", body, [1])]))
    assert result.status == "rejected" and result.reason == "words"


def test_three_failed_dreams_park_and_a_new_prompt_lifts_it():
    prompt("a recent question", "s1")

    def boom(system, user):
        raise TimeoutError("slow")

    for _ in range(3):
        assert dream(FakeLLMClient(boom)).status == "failed"
    parked = dream(dream_model([]))
    assert parked.reason == "parked"
    add_prompt(USER, "a newer question", session="s1", at=NOW + timedelta(hours=1))
    llm = dream_model([entry("answer_style", "", "You like short answers.", [1])])
    assert dream(llm).status == "applied"


@pytest.mark.django_db(transaction=True)
def test_model_calls_happen_outside_a_transaction():
    prompt("a recent question", "s1")
    seen = []

    def respond(system, user):
        seen.append(connection.in_atomic_block)
        if system == DREAM:
            return json.dumps({"sections": [entry("answer_style", "", "Short.", [1])]})
        return "no"

    assert dream(FakeLLMClient(respond)).status == "applied"
    assert len(seen) == 2 and not any(seen)


def test_a_held_lock_is_busy():
    assert quota.acquire_job_lock(USER, NOW)
    assert dream(dream_model([])).status == "busy"


def test_the_quota_applies():
    prompt("a recent question", "s1")
    hour = NOW.replace(minute=0, second=0, microsecond=0)
    for _ in range(quota.JOB_PER_HOUR_DEFAULT):
        quota.take_quotas("job", [(USER, hour, quota.JOB_PER_HOUR_DEFAULT)])
    llm = dream_model([entry("answer_style", "", "Short.", [1])])
    assert dream(llm).status == "quota" and llm.calls == []
    assert PersonalizationSettings.objects.filter(user_id=USER).count() == 0


def test_an_unchanged_dream_refreshes_dates_without_a_revision_bump_or_notice():
    old = NOW - timedelta(days=50)
    prompt("a recent question", "s1", days_ago=1)
    put(sec("Kind of answers you want", "You like short answers.", key="answer_style", at=old))
    before = settings_row()
    llm = dream_model([entry("answer_style", "", "You like short answers.", [1])])
    result = dream(llm)
    assert result.status == "no_change" and result.reason == "dream_unchanged"
    (section,) = store.get_sections(USER)
    assert section.last_supported_at > old
    assert section.last_supported_at >= NOW - timedelta(days=2)
    after = settings_row()
    assert after.profile_revision == before.profile_revision
    assert after.profile_updated_at == before.profile_updated_at
    assert after.notice_pending is False
