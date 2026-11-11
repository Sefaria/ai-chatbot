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
    assert seeded.status_code == 200 and seeded.data == {"seeded": 2}
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


# -- the lab page --------------------------------------------------------------------------------


def test_lab_page_404_without_flag(monkeypatch):
    monkeypatch.delenv("PERSONALIZATION_LAB_SEED", raising=False)
    assert Client().get("/lab/").status_code == 404


def test_lab_page_served_with_flag(seed_on):
    response = Client().get("/lab/")
    assert response.status_code == 200
    html = response.content.decode()
    assert "<title>LA personalization lab</title>" in html
    assert "/static/js/lc-chatbot.umd.cjs" in html and "hybrid" in html
