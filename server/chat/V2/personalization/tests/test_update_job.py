"""The single-call Profile job end to end with a fake model and a fake Jev. Made-up text only."""

import json
from datetime import timedelta

import pytest
from django.db import connection

from chat.models import PersonalizationSettings, ProfileWriterRun
from chat.V2.personalization import profile_store as store
from chat.V2.personalization import quota
from chat.V2.personalization.jev import FakeJevClient, JevAnswer
from chat.V2.personalization.llm import FakeLLMClient
from chat.V2.personalization.profile_job import run_profile_job

from .test_profile_store import NOW, add_prompt, section

pytestmark = pytest.mark.django_db

USER = "upd-job-user"


@pytest.fixture(autouse=True)
def env(settings):
    settings.PERSONALIZATION_JEV_GATE = "off"


def day(n):
    return NOW - timedelta(days=n)


def reply(*sections):
    return json.dumps({"sections": list(sections)})


def out(key, body, origin="inferred", support=(1,), heading=None):
    return {
        "key": key,
        "heading": heading or key or "x",
        "body": body,
        "origin": origin,
        "support": list(support),
    }


def model(*replies, check="no"):
    """A fake model: the update replies in order (the last repeats), a yes/no for the check."""
    queue = list(replies)

    def respond(system, user):
        if "yes or no" in system:
            return check
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return FakeLLMClient(respond)


def update_calls(llm):
    return [c for c in llm.calls if "yes or no" not in c["system"]]


def seed():
    add_prompt(USER, "what does Rashi say on the first verse", at=day(3))
    add_prompt(USER, "answer in Hebrew please", at=day(2))
    add_prompt(USER, "and Ramban on the same verse", at=day(1))


def put(*sections):
    assert store.replace_sections(
        USER,
        store.get_settings(USER).profile_revision,
        list(sections),
        updated_by="la",
        processed_through=None,
        set_notice=False,
    )


def run(llm, jev=None, **kw):
    return run_profile_job(USER, "run_now", now=NOW, llm=llm, jev=jev, **kw)


def stored():
    return {(s.key or s.heading): s for s in store.get_sections(USER)}


def good():
    return reply(
        out("reply_language", "You want answers in Hebrew.", "stated", [2]),
        out("commentators", "You look for Rashi and Ramban.", "inferred", [1, 3]),
    )


def test_first_run_applies_with_code_headings_and_support_dates():
    seed()
    result = run(model(good()))
    assert result.status == "applied" and result.reason is None
    rows = stored()
    assert rows["reply_language"].heading == "Reply language"
    assert rows["reply_language"].origin == "stated"
    assert rows["reply_language"].last_supported_at == day(2)
    assert rows["commentators"].last_supported_at == day(1)  # newest among prompts 1 and 3
    settings_ = store.get_settings(USER)
    assert settings_.profile_revision == 1 and settings_.last_run_at == NOW
    assert [s.key for s in store.get_sections(USER)] == ["reply_language", "commentators"]


def test_same_input_again_is_no_change_and_does_not_bump_the_revision():
    seed()
    llm = model(good())
    run(llm)
    again = run(llm)
    assert again.status == "no_change"
    assert store.get_settings(USER).profile_revision == 1
    assert ProfileWriterRun.objects.filter(user_id=USER, status="no_change").count() == 1


def test_an_invalid_reply_is_retried_once():
    seed()
    llm = model("not json", good())
    result = run(llm)
    assert (result.status, result.reason) == ("applied", "retry_succeeded")
    assert len(update_calls(llm)) == 2


def test_two_invalid_replies_keep_the_current_profile():
    seed()
    put(section("Reply language", "You want French.", key="reply_language", origin="stated"))
    llm = model("not json")
    result = run(llm)
    assert (result.status, result.reason) == ("failed", "validation_failed_kept_current")
    assert len(update_calls(llm)) == 2
    assert stored()["reply_language"].body == "You want French."
    row = ProfileWriterRun.objects.get(id=result.run_id)
    assert row.failed_check == "not_json_sections"
    assert store.get_settings(USER).last_run_at == NOW


def test_an_empty_valid_result_removes_non_user_sections_and_leaves_no_profile():
    seed()
    put(section("Reply language", "You want French.", key="reply_language", origin="stated"))
    result = run(model(reply()))
    assert (result.status, result.reason) == ("applied", "valid_empty")
    assert store.get_sections(USER) == []
    assert store.render_profile(store.get_sections(USER)) == ""


def test_an_empty_result_with_nothing_stored_is_a_no_change():
    seed()
    result = run(model(reply()))
    assert (result.status, result.reason) == ("no_change", "valid_empty")
    assert store.get_settings(USER).profile_revision == 0


def test_a_user_section_survives_an_output_that_omits_it():
    seed()
    mine = section("Reply language", "You asked for Yiddish.", key="reply_language", origin="user")
    mine.last_supported_at = day(40)
    put(mine, section("Commentators", "Old.", key="commentators", position=1))
    run(model(reply(out("answer_style", "You like short answers.", "stated", [3]))))
    row = stored()["reply_language"]
    assert (row.origin, row.body, row.last_supported_at) == ("user", mine.body, day(40))
    assert "commentators" not in stored()


def test_a_returned_user_section_keeps_its_date_and_an_inferred_claim_does_not_beat_it():
    seed()
    mine = section("Reply language", "You asked for Yiddish.", key="reply_language", origin="user")
    mine.last_supported_at = day(40)
    put(mine)
    run(model(reply(out("reply_language", "You write in English.", "inferred", [1, 2, 3]))))
    row = stored()["reply_language"]
    assert (row.origin, row.body, row.last_supported_at) == ("user", mine.body, day(40))


def test_a_later_outright_prompt_turns_a_user_section_stated():
    seed()
    put(section("Reply language", "You asked for Yiddish.", key="reply_language", origin="user"))
    result = run(model(reply(out("reply_language", "You want Hebrew.", "stated", [2]))))
    row = stored()["reply_language"]
    assert result.status == "applied"
    assert (row.origin, row.body, row.last_supported_at) == ("stated", "You want Hebrew.", day(2))


def test_a_never_store_section_is_dropped_alone():
    seed()
    result = run(
        model(
            reply(
                out("reply_language", "You want answers in Hebrew.", "stated", [2]),
                out(None, "You live in Tel Aviv.", "stated", [1], heading="Where you are"),
            )
        )
    )
    assert (result.status, result.reason) == ("applied", "never_store_rejected")
    assert list(stored()) == ["reply_language"]
    assert ProfileWriterRun.objects.get(id=result.run_id).failed_check == "never_store_terms"


def test_instructions_are_sent_only_when_on_and_not_empty():
    seed()
    store.set_instructions(USER, "Keep it short.")
    llm = model(reply())
    run(llm)
    assert "<instructions>" in llm.calls[0]["user"]
    store.set_enabled(USER, "instructions", False)
    llm = model(reply())
    run(llm)
    assert "<instructions>" not in llm.calls[0]["user"]


def test_prompts_before_a_clear_are_not_read():
    seed()
    PersonalizationSettings.objects.update_or_create(
        user_id=USER, defaults={"cleared_at": day(1) - timedelta(hours=1)}
    )
    add_prompt(USER, "a question after the clear", at=NOW - timedelta(minutes=1))
    llm = model(reply())
    run(llm)
    user = llm.calls[0]["user"]
    assert "after the clear" in user and "Rashi" not in user


def test_prompts_older_than_60_days_reach_the_input():
    add_prompt(USER, "an old question about Rambam", at=day(100))
    llm = model(reply())
    run(llm)
    assert "an old question about Rambam" in llm.calls[0]["user"]


def test_no_prompts_makes_no_call_and_still_marks_the_run():
    llm = model(reply())
    result = run(llm)
    assert (result.status, result.reason) == ("no_change", "no_prompts") and llm.calls == []
    assert store.get_settings(USER).last_run_at == NOW


def test_a_model_error_fails_without_marking_the_run():
    seed()

    def boom(system, user):
        raise TimeoutError("slow")

    result = run(FakeLLMClient(boom))
    assert result.status == "failed" and result.reason == "error:TimeoutError"
    assert store.get_settings(USER).last_run_at is None


def test_a_held_lock_is_busy_and_over_quota_makes_no_call(monkeypatch):
    seed()
    lease = quota.acquire_job_lock(USER, NOW)
    assert run(model(reply())).status == "busy"
    quota.release_job_lock(USER, lease)
    monkeypatch.setenv("PERSONALIZATION_JOB_PER_HOUR", "0")
    llm = model(reply())
    assert run(llm).status == "quota" and llm.calls == []


def test_jev_sensitive_prompts_are_dropped_before_numbering(settings, monkeypatch):
    settings.PERSONALIZATION_JEV_GATE = "on"
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-not-real")
    seed()
    jev = FakeJevClient(
        lambda state, qs: {
            k: JevAnswer("noul", noul=0.95 if k == "sensitive_1" else 0.1) for k in qs
        }
    )
    llm = model(reply())
    run(llm, jev=jev)
    user = llm.calls[0]["user"]
    assert "answer in Hebrew" not in user and "[P2] " in user and "[P3]" not in user


def test_the_run_row_holds_counts_and_codes_only():
    seed()
    result = run(model(good()))
    row = ProfileWriterRun.objects.get(id=result.run_id)
    summary = json.loads(row.extraction)
    assert summary["prompts_read"] == 3
    assert summary["sections"] == [
        {"key": "reply_language", "origin": "stated", "support": 1},
        {"key": "commentators", "origin": "inferred", "support": 2},
    ]
    assert "Hebrew" not in row.extraction


def test_model_calls_happen_outside_a_transaction():
    seed()
    seen = []

    def respond(system, user):
        seen.append(bool(connection.savepoint_ids))  # the test's own atomic has none
        return "no" if "yes or no" in system else good()

    assert run(FakeLLMClient(respond)).status == "applied"
    assert len(seen) == 3 and not any(seen)  # the update call and one check per section


def test_a_run_finishing_after_a_clear_records_no_pre_clear_text():
    seed()
    put(section("Reply language", "You want French.", key="reply_language", origin="stated"))

    def respond(system, user):
        if "yes or no" in system:
            return "no"
        store.clear_profile(USER)  # the user clears while the job is in flight
        return good()

    result = run(FakeLLMClient(respond))
    row = ProfileWriterRun.objects.get(pk=result.run_id)
    assert (row.status, row.reason) == ("failed", "revision_moved")
    assert row.extraction == "" and row.profile_before == "" and row.profile_after == ""
    assert not store.get_sections(USER)


def test_a_job_that_loses_the_revision_race_to_an_edit_does_not_clobber_it():
    seed()
    put(section("Reply language", "You want French.", key="reply_language", origin="stated"))
    edited = section(
        "Reply language", "You asked for Yiddish.", key="reply_language", origin="user"
    )
    base = model(good())

    def respond(system, user):
        if "yes or no" not in system:  # a chat-to-edit lands while the job is working
            rev = store.get_settings(USER).profile_revision
            assert store.replace_sections(
                USER, rev, [edited], updated_by="you_la", processed_through=None, set_notice=False
            )
        return base.responder(system, user)

    result = run(FakeLLMClient(respond))
    assert (result.status, result.reason) == ("failed", "revision_moved")
    assert stored()["reply_language"].body == edited.body
    assert store.get_settings(USER).profile_updated_by == "you_la"
    assert store.get_settings(USER).last_run_at is None  # not a completed run: retried
