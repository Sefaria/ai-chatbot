"""Who the nightly update runs for, and the live triggers staying off. Fake model only."""

import io
import json
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from chat.models import ProfileWriterRun
from chat.V2 import views
from chat.V2.personalization import profile_job, runtime
from chat.V2.personalization import profile_store as store
from chat.V2.personalization.llm import FakeLLMClient

from .test_profile_store import add_prompt

pytestmark = pytest.mark.django_db

USER = "night-user"


@pytest.fixture(autouse=True)
def env(monkeypatch, settings):
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("PERSONALIZATION_OVERRIDE_ALLOW_ALL", "true")
    settings.PERSONALIZATION_OVERRIDE_ENABLED = True
    settings.PERSONALIZATION_JEV_GATE = "off"


@pytest.fixture
def llm(monkeypatch):
    def respond(system, user):
        if "yes or no" in system:
            return "no"
        return json.dumps({"sections": []})

    fake = FakeLLMClient(respond)
    monkeypatch.setattr(profile_job, "AnthropicLLMClient", lambda: fake)
    return fake


def ago(**kw):
    return timezone.now() - timedelta(**kw)


def command(name, *args):
    out = io.StringIO()
    call_command(name, *args, stdout=out)
    return out.getvalue()


def due():
    return "dream dry run: 1 user(s) due" in command("personalization_dream", "--dry-run")


def test_never_run_user_with_prompts_is_due_and_not_after_a_run(llm):
    add_prompt(USER, "a question", at=ago(hours=5))
    assert due()
    assert "dream: 1 user(s) run" in command("personalization_dream")
    assert store.get_settings(USER).last_run_at is not None
    assert not due()


def test_second_invocation_the_same_night_finds_nobody(llm):
    add_prompt(USER, "a question", at=ago(hours=5))
    command("personalization_dream")
    assert "dream: 0 user(s) run (none)" in command("personalization_dream")
    assert llm.calls and len(llm.calls) == 1


def test_a_new_prompt_after_the_last_run_makes_the_user_due(llm):
    add_prompt(USER, "a question", at=ago(hours=5))
    command("personalization_dream")
    add_prompt(USER, "another question", at=timezone.now() + timedelta(seconds=1))
    assert due()


@pytest.mark.parametrize("change", ["text", "toggle"])
def test_an_instructions_change_after_the_last_run_makes_the_user_due(llm, change):
    add_prompt(USER, "a question", at=ago(hours=5))
    command("personalization_dream")
    assert not due()
    if change == "text":
        store.set_instructions(USER, "Be brief.")
    else:
        store.set_enabled(USER, "instructions", False)
    assert due()
    command("personalization_dream")
    assert not due()


def test_instructions_alone_make_a_never_run_user_due(llm):
    store.set_instructions(USER, "Be brief.")
    assert due()


def test_disabled_users_are_not_due(llm, settings):
    add_prompt(USER, "a question", at=ago(hours=5))
    add_prompt("anon:x", "a question", at=ago(hours=5))
    store.set_enabled(USER, "profile", False)
    assert not due()
    store.set_enabled(USER, "profile", True)
    settings.PERSONALIZATION_OVERRIDE_ENABLED = False
    assert not due()


def test_old_prompts_still_make_a_never_run_user_due(llm):
    add_prompt(USER, "an old question", at=ago(days=90))
    assert due()


def test_run_now_runs_the_job_whether_or_not_the_user_is_due(llm):
    command("personalization_dream")  # nothing is due
    result = profile_job.run_profile_job(USER, "run_now")
    assert result.status == "no_change"


# ---- live triggers -----------------------------------------------------------------------------


def test_sweep_is_a_no_op_while_live_triggers_are_off(llm, settings):
    settings.PERSONALIZATION_LIVE_TRIGGERS = False
    add_prompt(USER, "a question", at=ago(hours=5))
    assert "live triggers off" in command("personalization_sweep")
    assert ProfileWriterRun.objects.count() == 0 and llm.calls == []


@pytest.mark.parametrize("first_in_session", [True, False])
@pytest.mark.parametrize("live", [False, True])
def test_new_chat_and_every_message_start_a_job_only_when_live_triggers_are_on(
    monkeypatch, settings, first_in_session, live
):
    from types import SimpleNamespace

    from chat.models import ChatMessage

    settings.PERSONALIZATION_LIVE_TRIGGERS = live
    store.set_testing(USER, update_every_message=True)
    started = []
    monkeypatch.setattr(runtime, "_get_pool", lambda: pytest.fail("a job was queued"))
    monkeypatch.setattr(runtime, "queue_job", lambda user_id, trigger: started.append(trigger))
    views._queue_personalization_job(
        SimpleNamespace(is_anonymous=False),
        USER,
        plain=False,
        first_in_session=first_in_session,
        response_message=SimpleNamespace(status=ChatMessage.Status.SUCCESS),
    )
    expected = ["new_chat" if first_in_session else "every_message"] if live else []
    assert started == expected


def test_queue_job_itself_does_nothing_while_off(monkeypatch, settings):
    settings.PERSONALIZATION_LIVE_TRIGGERS = False
    monkeypatch.setattr(runtime, "_get_pool", lambda: pytest.fail("a job was queued"))
    runtime.queue_job(USER, "new_chat")


def test_the_seed_helper_writes_prompts_the_update_reads(llm):
    from .support.seed_live_user import seed

    assert seed() == 12
    prompts = store.unprocessed_user_prompts("live-check-user", None)
    assert len(prompts) == 12
    assert prompts[-1].server_timestamp < timezone.now() - timedelta(hours=1)
    assert due()
