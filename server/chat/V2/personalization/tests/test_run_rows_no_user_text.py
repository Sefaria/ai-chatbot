"""A run row holds no free text derived from the user, on every exit path. Each test drives one
path with distinctive made-up markers (English and Hebrew) in prompts, notes, headings, candidate
bodies and edit requests, then reads every run row back. Fake model and fake Jev only."""

import json
from datetime import timedelta

import pytest

from chat.models import ProfileWriterRun
from chat.V2.personalization import profile_store as store
from chat.V2.personalization.llm import FakeLLMClient
from chat.V2.personalization.profile_edit import edit_profile
from chat.V2.personalization.profile_job import run_profile_job
from chat.V2.prompts.chapters import load_chapter

from .run_rows import assert_run_rows_hold_no_user_text, run_row_text
from .test_profile_job import NOW, USER, item, model, put, section, seed
from .test_profile_store import add_prompt

pytestmark = pytest.mark.django_db

EN = "Teaneck-MARKER"
HE = "גר בחיפה-סימן"
BAD = [EN, HE]
EXTRACTION = load_chapter("profile_extraction")
WRITER_HINT = "## Output for this job"
DREAM = load_chapter("profile_dream")
EDIT = load_chapter("profile_edit")


@pytest.fixture(autouse=True)
def env(monkeypatch, settings):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-not-real")
    settings.PERSONALIZATION_JEV_GATE = "off"


def marked_prompts():
    seed((f"{EN} explain the first verse", "s1"), (f"{HE} short answers please", "s2"))


def marked_items():
    return [
        item(
            "answer_style",
            f"{EN} {HE}",
            sessions=1,
            notes=(f"note {EN}", f"הערה {HE}"),
        ),
        item(None, f"{EN} heading {HE}", action="none", notes=(f"dyn {EN}",)),
    ]


def run(llm, **kw):
    return run_profile_job(USER, kw.pop("trigger", "idle"), now=NOW, llm=llm, **kw)


def only_row():
    return ProfileWriterRun.objects.get(user_id=USER)


def rejected_body(body):
    return {"Kind of answers you want": body}


def test_the_helper_finds_a_planted_marker():
    marked_prompts()
    run(model(marked_items()))
    row = only_row()
    ProfileWriterRun.objects.filter(pk=row.pk).update(extraction=f"x {HE} y")
    with pytest.raises(AssertionError):
        assert_run_rows_hold_no_user_text(USER, BAD)
    assert HE in run_row_text(ProfileWriterRun.objects.get(pk=row.pk))


def test_applied():
    marked_prompts()
    result = run(model(marked_items()))
    assert result.status == "applied"
    assert_run_rows_hold_no_user_text(USER, BAD)
    row = only_row()
    assert row.profile_after == "# Kind of answers you want\nBody of Kind of answers you want."
    summary = json.loads(row.extraction)
    assert summary["prompts_read"] == 2 and summary["screened_out"] == 0


def test_no_change():
    marked_prompts()
    result = run(model([item(None, f"{EN} {HE}", action="none", notes=(f"n {EN}",))]))
    assert result.status == "no_change" and result.reason == "nothing_to_write"
    assert_run_rows_hold_no_user_text(USER, BAD)


def test_rejected_by_words():
    marked_prompts()
    result = run(model(marked_items(), rejected_body(f"{EN} {HE} " + "long " * 160)))
    assert result.reason == "words"
    assert_run_rows_hold_no_user_text(USER, BAD)
    assert only_row().profile_after == ""


def test_rejected_by_dynamic_cap():
    put(*[section(f"Topic {i}", "You read it.", kind="dynamic", position=i) for i in range(5)])
    marked_prompts()
    result = run(model(marked_items(), rejected_body(f"You like {EN} {HE}.")))
    assert result.status == "rejected" and result.reason == "dynamic_cap"
    assert_run_rows_hold_no_user_text(USER, BAD)
    assert only_row().profile_after == ""


def test_rejected_by_never_store_terms():
    marked_prompts()
    result = run(model(marked_items(), rejected_body(f"Your wife likes {EN} {HE}.")))
    assert result.reason == "never_store_terms"
    assert_run_rows_hold_no_user_text(USER, BAD)
    assert only_row().profile_after == ""


def test_rejected_by_never_store_model():
    marked_prompts()
    result = run(model(marked_items(), rejected_body(f"You like {EN} {HE}."), check="yes"))
    assert result.reason == "never_store_model"
    assert_run_rows_hold_no_user_text(USER, BAD)
    assert only_row().profile_after == ""


def test_rejected_by_never_store_unparseable():
    marked_prompts()
    result = run(model(marked_items(), rejected_body(f"You like {EN} {HE}."), check="maybe"))
    assert result.reason == "never_store_unparseable"
    assert_run_rows_hold_no_user_text(USER, BAD)
    assert only_row().profile_after == ""


def test_failed_by_bad_extraction():
    marked_prompts()
    result = run(FakeLLMClient(lambda s, u: f"not json {EN} {HE}"))
    assert result.status == "failed" and result.reason == "bad_extraction"
    assert_run_rows_hold_no_user_text(USER, BAD)


def test_failed_by_bad_writer_output():
    marked_prompts()
    base = model(marked_items())

    def respond(system, user):
        if WRITER_HINT in system:
            return json.dumps({"sections": [{"heading": f"{EN} other", "body": f"{HE}"}]})
        return base.responder(system, user)

    result = run(FakeLLMClient(respond))
    assert result.status == "failed" and result.reason == "bad_writer_output"
    assert_run_rows_hold_no_user_text(USER, BAD)


def test_failed_by_model_timeout():
    marked_prompts()
    base = model(marked_items())

    def respond(system, user):
        if WRITER_HINT in system:
            raise TimeoutError(f"slow {EN} {HE}")
        return base.responder(system, user)

    result = run(FakeLLMClient(respond))
    assert result.status == "failed" and result.reason == "error:TimeoutError"
    assert_run_rows_hold_no_user_text(USER, BAD)


def test_revision_moved():
    marked_prompts()
    base = model(marked_items())

    def respond(system, user):
        if WRITER_HINT in system:
            put(section("Reply language", "You write in English.", key="reply_language"))
        return base.responder(system, user)

    result = run(FakeLLMClient(respond))
    assert result.status == "failed" and result.reason == "revision_moved"
    assert_run_rows_hold_no_user_text(USER, BAD)
    assert only_row().profile_after == ""


def _dream_prompts():
    for text, session in ((f"{EN} one", "s1"), (f"{HE} two", "s2")):
        add_prompt(USER, text, session=session, at=NOW - timedelta(days=1))


def _dream(sections):
    def respond(system, user):
        if system == DREAM:
            return json.dumps({"sections": sections})
        return "no"

    return run_profile_job(USER, "dream", window="dream", now=NOW, llm=FakeLLMClient(respond))


def test_dream_applied():
    _dream_prompts()
    result = _dream(
        [
            {
                "key": "answer_style",
                "heading": f"{EN} {HE}",
                "body": "You like short answers.",
                "origin": "inferred",
                "support": [1, 2],
            },
            {  # one session only: dropped, its heading must not be stored
                "key": None,
                "heading": f"{HE} {EN} topic",
                "body": "You read widely.",
                "origin": "inferred",
                "support": [1],
            },
        ]
    )
    assert result.status == "applied"
    assert_run_rows_hold_no_user_text(USER, BAD)
    assert only_row().profile_after == "# Kind of answers you want\nYou like short answers."


def test_dream_unsupported_section():
    _dream_prompts()
    result = _dream(
        [
            {
                "key": None,
                "heading": f"{EN} {HE}",
                "body": f"You like {EN} {HE}.",
                "origin": "inferred",
                "support": [],
            }
        ]
    )
    assert result.status == "rejected" and result.reason == "unsupported_section"
    assert_run_rows_hold_no_user_text(USER, BAD)
    assert only_row().profile_after == ""


def test_parked():
    marked_prompts()
    for _ in range(3):
        assert run(FakeLLMClient(lambda s, u: f"not json {EN}")).status == "failed"
    assert run(model()).reason == "parked"
    assert_run_rows_hold_no_user_text(USER, BAD)


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


def test_the_summary_holds_counts_slot_keys_and_the_word_dynamic_only(settings):
    settings.PERSONALIZATION_JEV_GATE = "on"
    from .test_profile_job import jev_answering

    marked_prompts()
    items = [
        item("answer_style", "x", action="add", origin="stated", sessions=1),
        item(None, f"{EN} topic", action="update", sessions=2, notes=(f"n {EN}",)),
        item(None, f"{HE} other", action="none", sessions=0),
    ]
    run(model(items), jev=jev_answering(0.9, sensitive_0=0.1))
    summary = json.loads(only_row().extraction)
    assert summary["prompts_read"] == 2 and summary["screened_out"] == 1
    assert summary["gate"]["source"] == "jev" and isinstance(summary["gate"]["scores"], dict)
    assert summary["sections"] == [
        {"key": "answer_style", "action": "add", "origin": "stated", "sessions": 1},
        {"key": "dynamic", "action": "update", "origin": "inferred", "sessions": 1},  # clamped
        {"key": "dynamic", "action": "none", "origin": "inferred", "sessions": 0},
    ]
    assert set(summary) == {"prompts_read", "screened_out", "gate", "sections"}
    assert_run_rows_hold_no_user_text(USER, BAD)
