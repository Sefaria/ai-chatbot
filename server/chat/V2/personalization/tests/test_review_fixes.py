"""Review fixes: lock lease and per-user time, Profile switch off. Fake model and Jev, made-up text only."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from django.utils import timezone

from chat.models import ProfileWriterRun
from chat.V2.personalization import profile_edit, profile_job, quota, runtime
from chat.V2.personalization import profile_store as store
from chat.V2.personalization.llm import FakeLLMClient
from chat.V2.personalization.profile_job import run_profile_job
from chat.V2.personalization.profile_store import SectionData

from .test_profile_store import add_prompt

pytestmark = pytest.mark.django_db

USER = "user-rf"
T0 = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def env(monkeypatch, settings):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-not-real")
    settings.PERSONALIZATION_JEV_GATE = "off"
    settings.PERSONALIZATION_LIVE_TRIGGERS = True


def sec(heading, body, *, key=None, origin="inferred", at=None):
    return SectionData(key, "core" if key else "dynamic", heading, body, origin, at or NOW, 0)


def put(*sections, notice=False):
    rev = store.get_settings(USER).profile_revision
    assert store.replace_sections(
        USER, rev, list(sections), updated_by="la", processed_through=None, set_notice=notice
    )


def session(llm, trigger="run_now"):
    return run_profile_job(USER, trigger, now=NOW, llm=llm)


def counting_model(respond):
    calls = []

    def inner(system, user):
        calls.append(user)
        return respond(system, user)

    return FakeLLMClient(inner), calls


def boom(system, user):
    raise TimeoutError("slow")


def seed_batch():
    add_prompt(USER, "show me the commentary on the first verse", session="s1", at=T0)
    add_prompt(USER, "short answers please", session="s2", at=T0 + timedelta(minutes=1))


# ---- 3. lock lease and per-user time -----------------------------------------------------------


def test_a_worker_whose_lock_was_taken_over_cannot_release_the_new_holders_lock():
    t0 = NOW
    old = quota.acquire_job_lock(USER, t0)
    assert old
    new = quota.acquire_job_lock(USER, t0 + timedelta(seconds=quota.LOCK_STALE_SECONDS + 10))
    assert new and new != old
    quota.release_job_lock(USER, old)  # the stale worker finishing late
    assert not quota.acquire_job_lock(USER, t0 + timedelta(seconds=quota.LOCK_STALE_SECONDS + 20))
    quota.release_job_lock(USER, new)
    assert quota.acquire_job_lock(USER, t0 + timedelta(seconds=quota.LOCK_STALE_SECONDS + 30))


def test_the_commands_take_the_time_per_user(monkeypatch, settings):
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("PERSONALIZATION_OVERRIDE_ALLOW_ALL", "true")
    settings.PERSONALIZATION_OVERRIDE_ENABLED = True
    from django.core.management import call_command

    for user in ("a", "b"):
        add_prompt(user, "a question", session="s1", at=timezone.now() - timedelta(minutes=40))
    seen = []
    real = quota.acquire_job_lock

    def spy(user_id, now):
        seen.append((user_id, now))
        return real(user_id, now)

    monkeypatch.setattr(quota, "acquire_job_lock", spy)
    base = timezone.now()
    ticks = iter(base + timedelta(seconds=400 * n) for n in range(1000))
    monkeypatch.setattr(timezone, "now", lambda: next(ticks))
    monkeypatch.setattr(
        profile_job,
        "AnthropicLLMClient",
        lambda: FakeLLMClient(lambda s, u: json.dumps({"sections": []})),
    )
    call_command("personalization_sweep")
    assert [u for u, _ in seen] == ["a", "b"]
    assert seen[1][1] - seen[0][1] >= timedelta(seconds=400)


# ---- 4. Profile switch off ---------------------------------------------------------------------


def switch_off():
    store.set_enabled(USER, "profile", False)


def test_the_session_job_does_nothing_while_the_profile_is_off():
    seed_batch()
    switch_off()
    llm, calls = counting_model(boom)
    result = session(llm)
    assert (result.status, result.reason) == ("no_change", "profile_off")
    assert calls == []
    assert store.get_sections(USER) == [] and ProfileWriterRun.objects.count() == 0
    store.set_enabled(USER, "profile", True)  # activity from the off period is then processed
    llm2, calls2 = counting_model(lambda s, u: json.dumps({"sections": []}))
    session(llm2)
    assert calls2 and "commentary on the first verse" in " ".join(calls2)


def test_chat_to_edit_is_refused_while_the_profile_is_off():
    switch_off()
    llm, calls = counting_model(boom)
    status, reason = profile_edit.edit_profile(USER, "make it shorter", llm=llm)
    assert (status, reason) == ("refused", profile_edit.REASONS["profile_off"])
    assert calls == []
    assert ProfileWriterRun.objects.get(user_id=USER).reason == "profile_off"


def test_nothing_is_queued_for_a_user_with_the_profile_off(monkeypatch):
    seen = []
    monkeypatch.setattr(profile_job, "run_profile_job", lambda u, t: seen.append((u, t)))
    switch_off()
    runtime.queue_job(USER, "new_chat")
    assert runtime.wait_for_idle() and seen == []
    store.set_enabled(USER, "profile", True)
    runtime.queue_job(USER, "new_chat")
    assert runtime.wait_for_idle() and seen == [(USER, "new_chat")]
