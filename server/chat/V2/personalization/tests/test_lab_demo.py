"""The lab demo surface: anonymous override, persona seeding, the job actions and the lab page.

Every string here is synthetic."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import Client, override_settings

from chat.V2.personalization import gate, runtime
from chat.V2.personalization.job import prompts_sent
from chat.V2.personalization.llm import FakeLLMClient

from .test_api import OTHER_ID, SECRET, USER_ID, profile

pytestmark = pytest.mark.django_db

ANON = "anon:" + "a" * 64
TURNS = [
    {"user": "I read Hebrew pretty well and enjoy Rashi", "sessionId": "s1"},
    {
        "user": "Reading Rashi in Hebrew is comfortable for me by now",
        "assistant": "Understood.",
        "sessionId": "s2",
        "ts": "2026-01-01T00:00:00Z",
    },
]


def settings_for(**extra):
    return SimpleNamespace(
        PERSONALIZATION_OVERRIDE_ENABLED=True, PERSONALIZATION_OVERRIDE_USER_IDS="", **extra
    )


@pytest.fixture
def honoured():
    with override_settings(
        CHATBOT_USER_TOKEN_SECRET=SECRET,
        PERSONALIZATION_OVERRIDE_ENABLED=True,
        PERSONALIZATION_OVERRIDE_USER_IDS=USER_ID,
    ):
        yield


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "preview")
    monkeypatch.delenv("PERSONALIZATION_OVERRIDE_ALLOW_ANON", raising=False)


@pytest.fixture(autouse=True)
def clean_cache():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def seed_on(monkeypatch):
    monkeypatch.setenv("PERSONALIZATION_LAB_SEED", "true")


def stub_llm():
    def respond(system, user):
        ids = [r["id"] for r in prompts_sent(user)]
        return json.dumps(
            {
                "updates": [
                    {
                        "slot": "hebrew_fluency",
                        "value": "fluent",
                        "evidence": ids,
                        "correction": False,
                    }
                ],
                "conflicts": [],
            }
        )

    return FakeLLMClient(respond)


# -- anonymous override --------------------------------------------------------------------------


def test_anon_refused_without_flag(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "lab")
    monkeypatch.delenv("PERSONALIZATION_OVERRIDE_ALLOW_ANON", raising=False)
    assert gate.override_allowed(settings_for(), ANON) is False


def test_anon_allowed_with_flag_in_lab(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "lab")
    monkeypatch.setenv("PERSONALIZATION_OVERRIDE_ALLOW_ANON", "true")
    assert gate.override_allowed(settings_for(), ANON) is True
    assert gate.override_allowed(settings_for(), "someone-registered") is False


@pytest.mark.parametrize("environment", ["production", "prod", "beta", ""])
def test_anon_refused_in_production(monkeypatch, environment):
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("PERSONALIZATION_OVERRIDE_ALLOW_ANON", "true")
    assert gate.override_allowed(settings_for(), ANON) is False


def test_anon_refused_when_override_disabled(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "lab")
    monkeypatch.setenv("PERSONALIZATION_OVERRIDE_ALLOW_ANON", "true")
    disabled = SimpleNamespace(
        PERSONALIZATION_OVERRIDE_ENABLED=False, PERSONALIZATION_OVERRIDE_USER_IDS=""
    )
    assert gate.override_allowed(disabled, ANON) is False


# -- the new actions are 404 without the override ------------------------------------------------


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET, PERSONALIZATION_OVERRIDE_ENABLED=True)
@pytest.mark.parametrize(
    "action", ["run_job", "apply_candidate", "discard_candidate", "seed_persona"]
)
def test_new_actions_404_when_not_allowed(monkeypatch, seed_on, action):
    monkeypatch.setenv("ENVIRONMENT", "lab")
    with override_settings(PERSONALIZATION_OVERRIDE_USER_IDS=OTHER_ID):
        assert profile(action, turns=TURNS).status_code == 404


def test_seed_persona_404_without_its_flag(honoured, monkeypatch):
    monkeypatch.delenv("PERSONALIZATION_LAB_SEED", raising=False)
    assert profile("seed_persona", turns=TURNS).status_code == 404
    assert profile("get").status_code == 200


# -- seeding -------------------------------------------------------------------------------------


def test_seed_persona_rejects_bad_input(honoured, seed_on):
    too_many = [{"user": f"turn {i}"} for i in range(61)]
    assert profile("seed_persona", turns=too_many).status_code == 400
    assert profile("seed_persona", turns=[{"user": "x" * 4001}]).status_code == 400
    assert profile("seed_persona", turns=[{"user": ""}]).status_code == 400
    assert profile("seed_persona", turns=[{"user": "ok", "ts": "not a date"}]).status_code == 400
    assert profile("seed_persona").status_code == 400
    assert profile("get").data["observations"] == 0


def test_seed_then_run_job_then_apply(honoured, seed_on):
    seeded = profile("seed_persona", turns=TURNS)
    assert seeded.status_code == 200 and seeded.data == {"seeded": 2, "skipped": 0}
    assert profile("get").data["observations"] == 2

    with patch.object(runtime, "get_llm", return_value=stub_llm()):
        ran = profile("run_job")
    assert ran.status_code == 200
    body = ran.data
    assert body["status"] == "candidate" and body["observationsRead"] == 2
    assert body["changes"] == [
        {"slot": "hebrew_fluency", "old": None, "new": "fluent", "reason": "new"}
    ]
    assert body["conflicts"] == [] and isinstance(body["tokens"], int)
    assert isinstance(body["costUsd"], float) and isinstance(body["revision"], int)
    assert "hebrew_fluency" not in {s["slot"] for s in profile("get").data["slots"]}

    applied = profile("apply_candidate")
    assert applied.status_code == 200 and applied.data["applied"] is True
    slots = {s["slot"]: s for s in applied.data["profile"]["slots"]}
    assert slots["hebrew_fluency"]["value"] == "fluent"
    assert {s["slot"] for s in profile("get").data["slots"]} == set(slots)


def test_run_job_with_nothing_seeded_is_empty(honoured):
    with patch.object(runtime, "get_llm", return_value=stub_llm()):
        body = profile("run_job").data
    assert body["status"] == "empty" and body["changes"] == [] and body["observationsRead"] == 0


def test_stale_candidate_is_409(honoured, seed_on):
    profile("seed_persona", turns=TURNS)
    with patch.object(runtime, "get_llm", return_value=stub_llm()):
        assert profile("run_job").data["status"] == "candidate"
    # The learner sets the very slot the candidate would change.
    assert profile("set", slot="hebrew_fluency", value="basic").status_code == 200
    stale = profile("apply_candidate")
    assert stale.status_code == 409 and stale.data == {"error": "stale_revision"}


def test_discard_candidate(honoured, seed_on):
    profile("seed_persona", turns=TURNS)
    with patch.object(runtime, "get_llm", return_value=stub_llm()):
        profile("run_job")
    assert profile("discard_candidate").data == {"discarded": True}
    assert profile("get").data["slots"] == []
    assert profile("apply_candidate").status_code == 404


def test_seed_skips_sensitive_turns_and_drops_assistant_text(honoured, seed_on):
    turns = [
        {"user": "I am in mourning for my father", "assistant": "ASSISTANT-SECRET"},
        {"user": "I enjoy reading Rashi", "assistant": "ASSISTANT-SECRET"},
    ]
    response = profile("seed_persona", turns=turns)
    assert response.data == {"seeded": 1, "skipped": 1}
    stored = runtime.get_store().observations(USER_ID, include_quarantined=True)
    assert [o.text for o in stored] == ["I enjoy reading Rashi"]
    assert "preceding_assistant" not in stored[0].signals
    assert "ASSISTANT-SECRET" not in json.dumps(stored[0].signals)


def test_seed_rebases_timestamps_keeping_order_and_spacing(honoured, seed_on):
    turns = [
        {"user": "first", "ts": "2020-01-01T00:00:00Z"},
        {"user": "second", "ts": "2020-01-01T01:00:00Z"},
        {"user": "third", "ts": "2020-01-01T04:00:00Z"},
    ]
    assert profile("seed_persona", turns=turns).data["seeded"] == 3
    stored = sorted(runtime.get_store().observations(USER_ID), key=lambda o: o.text)
    by_text = {o.text: o.at for o in stored}
    from django.utils import timezone as tz

    assert abs((tz.now() - by_text["third"]).total_seconds() - 60) < 30
    assert (by_text["second"] - by_text["first"]).total_seconds() == 3600
    assert (by_text["third"] - by_text["second"]).total_seconds() == 3 * 3600


def test_seed_compresses_a_span_over_the_window(honoured, seed_on):
    turns = [
        {"user": "old", "ts": "2019-01-01T00:00:00Z"},
        {"user": "mid", "ts": "2019-02-01T00:00:00Z"},
        {"user": "new", "ts": "2019-03-01T00:00:00Z"},
    ]
    profile("seed_persona", turns=turns)
    at = {o.text: o.at for o in runtime.get_store().observations(USER_ID)}
    assert at["old"] < at["mid"] < at["new"]
    from django.utils import timezone as tz

    assert (tz.now() - at["old"]).days <= 29


@pytest.mark.parametrize(
    "ts", ["0000-00-00T00:00:00Z", "2026-13-45T00:00:00Z", "9999-12-31T23:59:59+99:00"]
)
def test_seed_bad_timestamp_is_400(honoured, seed_on, ts):
    assert profile("seed_persona", turns=[{"user": "ok", "ts": ts}]).status_code == 400


# -- run_job limits and the pending candidate -----------------------------------------------------


def test_run_job_pending_candidate_is_returned_when_window_unchanged(honoured, seed_on):
    profile("seed_persona", turns=TURNS)
    assert profile("get").data["pendingCandidate"] is False
    with patch.object(runtime, "get_llm", return_value=stub_llm()):
        first = profile("run_job").data
        again = profile("run_job").data
    assert first["status"] == "candidate"
    assert profile("get").data["pendingCandidate"] is True
    assert again["status"] == "candidate" and again["changes"] == first["changes"]
    profile("discard_candidate")
    assert profile("get").data["pendingCandidate"] is False


def test_run_job_per_hour_limit(honoured, seed_on, monkeypatch):
    monkeypatch.setenv("PERSONALIZATION_LAB_JOB_PER_HOUR", "2")
    with patch.object(runtime, "get_llm", return_value=stub_llm()):
        assert profile("run_job").status_code == 200
        assert profile("run_job").status_code == 200
        limited = profile("run_job")
    assert limited.status_code == 429 and limited.data == {"error": "rate_limited"}


def test_run_job_per_day_limit_is_deployment_wide(honoured, seed_on, monkeypatch):
    monkeypatch.setenv("PERSONALIZATION_LAB_JOB_PER_DAY", "1")
    with patch.object(runtime, "get_llm", return_value=stub_llm()):
        assert profile("run_job").status_code == 200
        assert profile("run_job").status_code == 429


def test_run_job_refuses_a_concurrent_call(honoured, seed_on):
    from django.core.cache import cache

    cache.add(f"lab-job-lock:{USER_ID}", 1, timeout=60)
    with patch.object(runtime, "get_llm", return_value=stub_llm()):
        response = profile("run_job")
    assert response.status_code == 429 and response.data == {"error": "rate_limited"}
    cache.delete(f"lab-job-lock:{USER_ID}")
    with patch.object(runtime, "get_llm", return_value=stub_llm()):
        assert profile("run_job").status_code == 200


def test_run_job_releases_its_lock(honoured, seed_on):
    with patch.object(runtime, "get_llm", return_value=stub_llm()):
        profile("run_job")
        assert profile("run_job").status_code == 200


# -- the lab page --------------------------------------------------------------------------------


def test_lab_page_404_without_flag(monkeypatch):
    monkeypatch.delenv("PERSONALIZATION_LAB_SEED", raising=False)
    assert Client().get("/lab/").status_code == 404


@pytest.mark.parametrize("environment", ["prod", "production", "beta", ""])
def test_lab_page_404_in_production_or_unset_environment(seed_on, monkeypatch, environment):
    monkeypatch.setenv("ENVIRONMENT", environment)
    assert Client().get("/lab/").status_code == 404


def test_lab_page_404_when_environment_unset(seed_on, monkeypatch):
    monkeypatch.delenv("ENVIRONMENT")
    assert Client().get("/lab/").status_code == 404


def test_lab_page_served_with_flag(seed_on):
    response = Client().get("/lab/")
    assert response.status_code == 200
    html = response.content.decode()
    assert "<title>LA personalization lab</title>" in html
    assert "/static/js/lc-chatbot.umd.cjs" in html and "hybrid" in html


# -- the lab origin ------------------------------------------------------------------------------

LAB_HOST = "lab.example.test"
LAB_ORIGIN = f"https://{LAB_HOST}"
ANON_UUID = "3f2b8c1e-5d4a-4b6f-9a7e-1c2d3e4f5a6b"


def anon_post(origin):
    from rest_framework.test import APIClient

    return APIClient().post(
        "/api/v2/personalization/profile",
        {"anonId": ANON_UUID, "action": "get"},
        format="json",
        HTTP_ORIGIN=origin,
    )


@pytest.fixture
def lab_env(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "lab")
    monkeypatch.setenv("PERSONALIZATION_LAB_SEED", "true")
    monkeypatch.setenv("PERSONALIZATION_LAB_ORIGINS", LAB_HOST)
    monkeypatch.setenv("PERSONALIZATION_OVERRIDE_ALLOW_ANON", "true")
    monkeypatch.delenv("CHATBOT_ALLOWED_SEFARIA_HOSTS", raising=False)


@override_settings(PERSONALIZATION_OVERRIDE_ENABLED=True)
def test_lab_origin_lets_anon_use_the_profile_api(lab_env):
    assert anon_post(LAB_ORIGIN).status_code == 200
    assert anon_post("https://other.example.test").status_code == 401


@override_settings(PERSONALIZATION_OVERRIDE_ENABLED=True)
def test_lab_origin_needs_the_seed_flag(lab_env, monkeypatch):
    monkeypatch.delenv("PERSONALIZATION_LAB_SEED")
    assert anon_post(LAB_ORIGIN).status_code == 401


@override_settings(PERSONALIZATION_OVERRIDE_ENABLED=True)
@pytest.mark.parametrize("environment", ["production", "prod", "beta", ""])
def test_lab_origin_refused_in_production(lab_env, monkeypatch, environment):
    monkeypatch.setenv("ENVIRONMENT", environment)
    assert anon_post(LAB_ORIGIN).status_code == 401


def test_lab_origin_keeps_the_default_sefaria_base_url(lab_env):
    from chat.sefaria_base_url import allowed_sefaria_base_url, lab_origin_allowed

    assert lab_origin_allowed(LAB_ORIGIN) is True
    assert allowed_sefaria_base_url(LAB_ORIGIN) is None  # tools and link checks use the default
    assert lab_origin_allowed(f"https://user@{LAB_HOST}") is False


@override_settings(PERSONALIZATION_OVERRIDE_ENABLED=True)
def test_anon_run_batch_is_rate_limited(lab_env, monkeypatch):
    from rest_framework.test import APIClient

    monkeypatch.setenv("PERSONALIZATION_LAB_JOB_PER_HOUR", "0")
    response = APIClient().post(
        "/api/v2/personalization/profile",
        {"anonId": ANON_UUID, "action": "run_batch", "mode": "hybrid"},
        format="json",
        HTTP_ORIGIN=LAB_ORIGIN,
    )
    assert response.status_code == 429 and response.data == {"error": "rate_limited"}
