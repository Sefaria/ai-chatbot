"""Triggers: the session-end sweep, the nightly dream command, job health, the dream run-now
action. Fake model only; the commands go through `call_command`."""

import io
import json
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from chat.models import ProfileWriterRun
from chat.V2.personalization import health, profile_job, quota
from chat.V2.personalization import profile_store as store
from chat.V2.personalization.llm import FakeLLMClient
from chat.V2.prompts.chapters import load_chapter

from .test_profile_store import add_prompt

pytestmark = pytest.mark.django_db

EXTRACTION = load_chapter("profile_extraction")
DREAM = load_chapter("profile_dream")


@pytest.fixture(autouse=True)
def env(monkeypatch, settings):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-not-real")
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("PERSONALIZATION_OVERRIDE_ALLOW_ALL", "true")
    settings.PERSONALIZATION_OVERRIDE_ENABLED = True
    settings.PERSONALIZATION_JEV_GATE = "off"


@pytest.fixture
def llm(monkeypatch):
    """One fake model for the whole test; the job builds its client from `AnthropicLLMClient`."""

    def respond(system, user):
        if system == EXTRACTION:
            return json.dumps(
                {
                    "sections": [
                        {
                            "key": "answer_style",
                            "heading": "Kind of answers you want",
                            "action": "add",
                            "origin": "inferred",
                            "notes": ["short"],
                            "sessions": 1,
                        }
                    ]
                }
            )
        if system == DREAM:
            return json.dumps(
                {
                    "sections": [
                        {
                            "key": "answer_style",
                            "heading": "",
                            "body": "You like short answers.",
                            "origin": "inferred",
                            "support": [1],
                        }
                    ]
                }
            )
        if "yes or no" in system:
            return "no"
        return json.dumps(
            {
                "sections": [
                    {"heading": "Kind of answers you want", "body": "You like short answers."}
                ]
            }
        )

    fake = FakeLLMClient(respond)
    monkeypatch.setattr(profile_job, "AnthropicLLMClient", lambda: fake)
    return fake


def ago(minutes):
    return timezone.now() - timedelta(minutes=minutes)


def say(user, text, minutes, session="s1"):
    add_prompt(user, text, session=session, at=ago(minutes))


def command(name, *args):
    out = io.StringIO()
    call_command(name, *args, stdout=out)
    return out.getvalue()


def runs(window="session"):
    return ProfileWriterRun.objects.filter(window=window).count()


# ---- sweep -------------------------------------------------------------------------------------


def test_sweep_runs_users_idle_for_31_minutes_and_not_29(llm):
    say("idle-user", "a question", 31)
    say("busy-user", "a question", 29)
    command("personalization_sweep")
    assert runs() == 1
    assert ProfileWriterRun.objects.get().user_id == "idle-user"
    assert store.get_sections("idle-user") and not store.get_sections("busy-user")


def test_the_newest_prompt_decides_not_the_oldest(llm):
    say("u", "an old question", 90)
    say("u", "a fresh question", 5)
    command("personalization_sweep")
    assert runs() == 0


def test_sweep_twice_in_a_row_runs_the_job_once(llm):
    say("u", "a question", 40)
    command("personalization_sweep")
    command("personalization_sweep")
    assert runs() == 1 and len(llm.calls) == 3  # extraction, writer, output check


def test_sweep_after_a_nothing_to_say_run_does_not_run_again(llm, settings):
    say("u", "a question", 40)
    llm.responder = lambda system, user: json.dumps({"sections": []})
    command("personalization_sweep")
    command("personalization_sweep")
    assert runs() == 1


def test_sweep_dry_run_runs_nothing(llm):
    say("u", "a question", 40)
    assert "1 user(s) due" in command("personalization_sweep", "--dry-run")
    assert runs() == 0 and llm.calls == []


def test_sweep_skips_anonymous_ids_and_a_gate_that_is_off(llm, settings):
    say("anon:abc", "a question", 40)
    command("personalization_sweep")
    assert runs() == 0
    say("u", "a question", 40)
    settings.PERSONALIZATION_OVERRIDE_ENABLED = False
    command("personalization_sweep")
    assert runs() == 0


def test_sweep_ignores_hidden_sessions(llm):
    from chat.models import ChatSession

    say("u", "a hidden question", 40, session="hid")
    ChatSession.objects.create(session_id="hid", user_id="u", is_deleted=True)
    command("personalization_sweep")
    assert runs() == 0


def test_sweep_skips_a_user_who_is_already_running(llm):
    say("u", "a question", 40)
    assert quota.acquire_job_lock("u", timezone.now())
    command("personalization_sweep")
    assert runs() == 0


# ---- dream command -----------------------------------------------------------------------------


def test_dream_runs_for_a_user_active_since_their_last_dream_and_for_no_one_else(llm):
    say("active", "a question", 600)
    say("quiet", "a question", 600)
    command("personalization_dream")
    assert runs("dream") == 2
    command("personalization_dream")  # nobody active since
    assert runs("dream") == 2
    say("active", "another question", -1)  # after the first dream started
    command("personalization_dream")
    assert runs("dream") == 3
    assert ProfileWriterRun.objects.filter(window="dream", user_id="active").count() == 2
    assert ProfileWriterRun.objects.filter(window="dream", user_id="quiet").count() == 1


def test_dream_ignores_prompts_older_than_the_window(llm):
    add_prompt("old", "an ancient question", session="s1", at=timezone.now() - timedelta(days=70))
    command("personalization_dream")
    assert runs("dream") == 0


def test_dream_dry_run_runs_nothing(llm):
    say("u", "a question", 600)
    assert "1 user(s) due" in command("personalization_dream", "--dry-run")
    assert runs("dream") == 0 and llm.calls == []


def test_a_failed_dream_is_retried_the_next_night(llm):
    say("u", "a question", 600)
    good = llm.responder

    def boom(system, user):
        raise TimeoutError("slow")

    llm.responder = boom
    command("personalization_dream")
    assert ProfileWriterRun.objects.get(window="dream").status == "failed"
    llm.responder = good
    command("personalization_dream")
    assert runs("dream") == 2
    assert ProfileWriterRun.objects.filter(window="dream", status="applied").count() == 1


# ---- health ------------------------------------------------------------------------------------


def by_name(snapshot):
    return {j["name"]: j for j in snapshot["jobs"]}


def test_health_is_empty_before_any_run():
    jobs = by_name(health.snapshot())
    assert set(jobs) == {"session", "dream"}
    assert jobs["session"] == {
        "name": "session",
        "lastRunAt": None,
        "lastWriteAt": None,
        "parked": 0,
    }


def test_a_run_that_wrote_sets_last_write_and_a_no_change_run_moves_only_last_run(llm):
    say("u", "a question", 40)
    assert profile_job.run_profile_job("u", "idle", llm=llm).status == "applied"
    first = by_name(health.snapshot())["session"]
    assert first["lastRunAt"] and first["lastWriteAt"] == first["lastRunAt"]
    # Nothing new: a no_change run.
    assert profile_job.run_profile_job("u", "idle", llm=llm).status == "no_change"
    second = by_name(health.snapshot())["session"]
    assert (
        second["lastRunAt"] >= first["lastRunAt"] and second["lastWriteAt"] == first["lastWriteAt"]
    )
    assert ProfileWriterRun.objects.filter(status="no_change").count() == 1
    assert by_name(health.snapshot())["dream"]["lastRunAt"] is None


def test_the_dream_has_its_own_row(llm):
    say("u", "a question", 40)
    assert profile_job.run_profile_job("u", "dream", window="dream", llm=llm).status == "applied"
    jobs = by_name(health.snapshot())
    assert jobs["dream"]["lastWriteAt"] and jobs["session"]["lastRunAt"] is None


def _fail_three_times(user):
    def boom(system, user_text):
        raise TimeoutError("slow")

    for _ in range(3):
        profile_job.run_profile_job(user, "idle", llm=FakeLLMClient(boom))


def test_a_parked_user_is_counted_and_a_new_prompt_uncounts_them():
    add_prompt("u", "a question", session="s1", at=ago(120))
    _fail_three_times("u")
    assert by_name(health.snapshot())["session"]["parked"] == 1
    say("u", "a newer question", -1)  # after the last failed run started
    assert by_name(health.snapshot())["session"]["parked"] == 0


def test_parked_counts_each_user_once():
    for user in ("a", "b"):
        add_prompt(user, "a question", session="s1", at=ago(120))
        _fail_three_times(user)
    assert by_name(health.snapshot())["session"]["parked"] == 2
    assert by_name(health.snapshot())["dream"]["parked"] == 0
