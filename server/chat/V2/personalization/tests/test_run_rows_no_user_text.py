"""A run row holds no free text derived from the user, on every exit path. Each test drives one
path with distinctive made-up markers (English and Hebrew) in prompts, headings, bodies and edit
requests, then reads every run row back. Fake model and fake Jev only."""

import json

import pytest

from chat.models import ProfileWriterRun
from chat.V2.personalization import profile_store as store
from chat.V2.personalization.llm import FakeLLMClient
from chat.V2.personalization.profile_edit import edit_profile
from chat.V2.personalization.profile_job import run_profile_job
from chat.V2.prompts.chapters import load_chapter

from .run_rows import assert_run_rows_hold_no_user_text, run_row_text
from .test_profile_store import NOW, add_prompt, section
from .test_update_job import USER, day, model, out, put, reply

pytestmark = pytest.mark.django_db

EN = "Teaneck-MARKER"
HE = "גר בחיפה-סימן"
BAD = [EN, HE]
EDIT = load_chapter("profile_alter")


@pytest.fixture(autouse=True)
def env(monkeypatch, settings):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-not-real")
    settings.PERSONALIZATION_JEV_GATE = "off"


def marked_prompts():
    add_prompt(USER, f"{EN} explain the first verse", session="s1", at=day(2))
    add_prompt(USER, f"{HE} short answers please", session="s2", at=day(1))


def marked_reply(body=f"You like short answers {EN} {HE}."):
    return reply(
        out("answer_style", body, "stated", [2]),
        out(None, f"{EN} heading {HE}", "inferred", [1, 2], heading=f"{EN} topic {HE}"),
    )


def run(llm, **kw):
    return run_profile_job(USER, kw.pop("trigger", "run_now"), now=NOW, llm=llm, **kw)


def only_row():
    return ProfileWriterRun.objects.get(user_id=USER)


def test_the_helper_finds_a_planted_marker():
    marked_prompts()
    run(model(marked_reply()))
    row = only_row()
    ProfileWriterRun.objects.filter(pk=row.pk).update(extraction=f"x {HE} y")
    with pytest.raises(AssertionError):
        assert_run_rows_hold_no_user_text(USER, BAD)
    assert HE in run_row_text(ProfileWriterRun.objects.get(pk=row.pk))


def test_applied():
    marked_prompts()
    result = run(model(marked_reply()))
    assert result.status == "applied"
    row = only_row()
    # profile_after is the stored Profile by design; every other field holds no user text
    assert row.profile_after.startswith("# Kind of answers you want\n")
    ProfileWriterRun.objects.filter(pk=row.pk).update(profile_after="", profile_before="")
    assert_run_rows_hold_no_user_text(USER, BAD)
    summary = json.loads(row.extraction)
    assert summary["prompts_read"] == 2 and set(summary) == {"prompts_read", "gate", "sections"}


def test_no_change():
    marked_prompts()
    llm = model(marked_reply())
    run(llm)
    ProfileWriterRun.objects.all().delete()
    assert run(llm).status == "no_change"
    ProfileWriterRun.objects.update(profile_before="")
    assert_run_rows_hold_no_user_text(USER, BAD)


def test_valid_empty():
    marked_prompts()
    assert run(model(reply())).reason == "valid_empty"
    assert_run_rows_hold_no_user_text(USER, BAD)


def test_never_store_section_dropped():
    marked_prompts()
    result = run(model(reply(out("answer_style", f"You live in {EN} {HE}.", "stated", [2]))))
    assert result.reason == "never_store_rejected"
    assert_run_rows_hold_no_user_text(USER, BAD)


def test_validation_failed_kept_current():
    marked_prompts()
    result = run(FakeLLMClient(lambda s, u: f"not json {EN} {HE}"))
    assert result.reason == "validation_failed_kept_current"
    assert_run_rows_hold_no_user_text(USER, BAD)


def test_failed_by_model_timeout():
    marked_prompts()

    def boom(system, user):
        raise TimeoutError(f"slow {EN} {HE}")

    result = run(FakeLLMClient(boom))
    assert result.status == "failed" and result.reason == "error:TimeoutError"
    assert_run_rows_hold_no_user_text(USER, BAD)


def test_revision_moved():
    marked_prompts()
    base = model(marked_reply())

    def respond(system, user):
        if "yes or no" not in system:
            put(section("Reply language", "You write in English.", key="reply_language"))
        return base.responder(system, user)

    result = run(FakeLLMClient(respond))
    assert result.status == "failed" and result.reason == "revision_moved"
    row = only_row()
    assert row.profile_after == ""
    ProfileWriterRun.objects.update(profile_before="")
    assert_run_rows_hold_no_user_text(USER, BAD)


def test_the_summary_holds_counts_slot_keys_and_the_word_dynamic_only(settings):
    from chat.V2.personalization.jev import FakeJevClient, JevAnswer

    settings.PERSONALIZATION_JEV_GATE = "on"
    marked_prompts()
    jev = FakeJevClient(
        lambda state, qs: {
            k: JevAnswer("noul", noul=0.1 if k.startswith("sensitive") else 0.9) for k in qs
        }
    )
    run(model(marked_reply()), jev=jev)
    summary = json.loads(only_row().extraction)
    assert summary["prompts_read"] == 2
    assert summary["gate"]["source"] == "jev" and isinstance(summary["gate"]["scores"], dict)
    assert summary["sections"] == [
        {"key": "answer_style", "origin": "stated", "support": 1},
        {"key": "dynamic", "origin": "inferred", "support": 2},
    ]


# ---- chat-to-edit ----


def _put_interests():
    put(
        section("Current interests", "You study many books.", key="current_interests"),
        section("Kind of answers you want", "You like short answers.", key="answer_style"),
    )


def _edit_model(reply, check="no"):
    def respond(system, user):
        return json.dumps(reply) if system == EDIT else check

    return FakeLLMClient(respond)


def _reply(body="You study parsha.", reason=f"done {EN} {HE}"):
    return {
        "status": "applied",
        "reason": reason,
        "sections": [{"key": "current_interests", "heading": f"{EN}", "body": body}],
    }


def test_edit_applied():
    _put_interests()
    status, _ = edit_profile(USER, f"only parsha {EN} {HE}", llm=_edit_model(_reply()))
    assert status == "applied"
    assert_run_rows_hold_no_user_text(USER, BAD)
    row = only_row()
    assert "You study parsha." in row.profile_after and row.reason == "applied"


def test_edit_refused_by_code():
    _put_interests()
    status, _ = edit_profile(USER, f"I am based in {EN} {HE}", llm=_edit_model({}))
    assert status == "refused"
    assert_run_rows_hold_no_user_text(USER, BAD)
    assert only_row().reason == "never_store"


def test_edit_refused_by_model():
    _put_interests()
    llm = _edit_model({"status": "refused", "reason": f"too vague {EN} {HE}"})
    assert edit_profile(USER, f"make it better {EN} {HE}", llm=llm)[0] == "refused"
    assert_run_rows_hold_no_user_text(USER, BAD)
    assert only_row().reason == "model_refused"


def test_edit_rejected_by_a_check():
    _put_interests()
    reply = _reply(body=f"Your wife likes {EN} {HE}.")
    assert edit_profile(USER, f"only parsha {EN}", llm=_edit_model(reply))[0] == "refused"
    assert_run_rows_hold_no_user_text(USER, BAD)
    assert only_row().profile_after == ""


def test_edit_failed():
    _put_interests()
    assert (
        edit_profile(USER, f"change it {EN} {HE}", llm=FakeLLMClient(lambda s, u: f"x {EN}"))[0]
        == "refused"
    )

    def boom(system, user):
        raise RuntimeError(f"api down {EN} {HE}")

    edit_profile(USER, f"change it again {EN} {HE}", llm=FakeLLMClient(boom))
    assert_run_rows_hold_no_user_text(USER, BAD)
    assert set(ProfileWriterRun.objects.values_list("status", flat=True)) == {"failed"}
    assert store.get_sections(USER)[0].body == "You study many books."
