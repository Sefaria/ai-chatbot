"""The settings endpoint: the access-control surface of personalization. Made-up text only."""

import hashlib
import json
import logging
import types
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from chat.models import ProfileWriterRun
from chat.tests.test_streaming_integration import create_test_token
from chat.V2.personalization import profile_edit, profile_job, quota
from chat.V2.personalization import profile_store as store
from chat.V2.personalization.llm import FakeLLMClient
from chat.V2.personalization.profile_store import SectionData
from chat.V2.prompts.chapters import load_chapter

pytestmark = pytest.mark.django_db

URL = "/api/v2/personalization/settings"
SECRET = "test-secret-for-settings"
USER_A = "user-a-1"
USER_B = "user-b-2"


@pytest.fixture(autouse=True)
def gate_on(settings, monkeypatch):
    settings.PERSONALIZATION_OVERRIDE_ENABLED = True
    settings.CHATBOT_USER_TOKEN_SECRET = SECRET
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("PERSONALIZATION_OVERRIDE_ALLOW_ALL", "true")
    monkeypatch.delenv("PERSONALIZATION_LAB_SEED", raising=False)


@pytest.fixture
def logs(caplog):
    """Capture the chat loggers too: the `chat` logger does not propagate to the root."""
    names = ("chat", "django.request")
    for name in names:
        logging.getLogger(name).addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG)
    for name in names:
        logging.getLogger(name).setLevel(logging.DEBUG)
    yield caplog
    for name in names:
        logging.getLogger(name).removeHandler(caplog.handler)


@pytest.fixture
def lab(monkeypatch):
    monkeypatch.setenv("PERSONALIZATION_LAB_SEED", "true")


@pytest.fixture
def client():
    return APIClient()


def token(user=USER_A, **kw):
    return create_test_token(user, SECRET, **kw)


def call(client, action, user=USER_A, headers=None, **fields):
    body = {"userId": token(user), "action": action, **fields}
    return client.post(URL, body, format="json", **(headers or {}))


def test_get_default_state(client):
    r = call(client, "get")
    assert r.status_code == 200
    assert r["Cache-Control"] == "no-store, private"
    assert r.json() == {
        "instructions": {"text": "", "enabled": True, "updatedAt": None},
        "profile": {
            "text": "",
            "sections": [],
            "enabled": True,
            "updatedAt": None,
            "updatedBy": None,
            "revision": 0,
        },
        "labEnabled": False,
        "testing": {"showPlainResponse": False, "updateEveryMessage": False},
    }


def test_state_shows_sections_and_lab_flag(client, lab):
    store.replace_sections(
        USER_A,
        0,
        [
            SectionData(
                key="reply_language",
                kind="core",
                heading="Reply language",
                body="English.",
                origin="inferred",
                last_supported_at=timezone.now(),
                position=0,
            )
        ],
        updated_by="la",
        processed_through=None,
        set_notice=False,
    )
    body = call(client, "get").json()
    assert body["labEnabled"] is True
    assert body["profile"]["sections"] == [
        {"heading": "Reply language", "body": "English.", "kind": "core"}
    ]
    assert body["profile"]["text"] == "# Reply language\nEnglish."
    assert body["profile"]["updatedBy"] == "la"
    assert body["profile"]["revision"] == 1


def test_set_instructions_300_ok_301_rejected(client):
    ok = call(client, "set_instructions", text="a" * 300)
    assert ok.status_code == 200
    assert ok.json()["instructions"]["text"] == "a" * 300
    assert ok.json()["instructions"]["updatedAt"] is not None
    bad = call(client, "set_instructions", text="a" * 301)
    assert bad.status_code == 400
    assert bad.json() == {"error": "too_long", "max": 300}
    assert call(client, "get").json()["instructions"]["text"] == "a" * 300


def test_set_instructions_needs_a_string(client):
    assert call(client, "set_instructions").status_code == 400
    assert call(client, "set_instructions", text=5).status_code == 400


def test_toggle(client):
    r = call(client, "toggle", target="profile", enabled=False)
    assert r.status_code == 200
    assert r.json()["profile"]["enabled"] is False
    assert r.json()["instructions"]["enabled"] is True
    r = call(client, "toggle", target="instructions", enabled=False)
    assert r.json()["instructions"]["enabled"] is False
    assert call(client, "toggle", target="other", enabled=True).status_code == 400
    assert call(client, "toggle", target="profile", enabled="yes").status_code == 400
    assert call(client, "toggle", target="profile").status_code == 400


def _audit_lines(caplog):
    return [r.getMessage() for r in caplog.records if "personalization_audit" in r.getMessage()]


def test_set_instructions_empty_to_empty_is_a_no_op(client, logs):
    r = call(client, "set_instructions", text="")
    assert r.status_code == 200
    assert r.json()["instructions"]["updatedAt"] is None
    r = call(client, "set_instructions", text="   ")
    assert r.json()["instructions"]["updatedAt"] is None
    assert _audit_lines(logs) == []


def test_set_instructions_same_text_keeps_the_date_and_logs_nothing(client, logs):
    first = call(client, "set_instructions", text="be brief").json()["instructions"]["updatedAt"]
    assert first is not None
    assert len(_audit_lines(logs)) == 1
    again = call(client, "set_instructions", text=" be brief ")
    assert again.json()["instructions"]["updatedAt"] == first
    assert len(_audit_lines(logs)) == 1


def test_set_instructions_text_to_empty_sets_the_date(client, logs):
    first = call(client, "set_instructions", text="be brief").json()["instructions"]["updatedAt"]
    r = call(client, "set_instructions", text="").json()["instructions"]
    assert r["text"] == "" and r["updatedAt"] is not None and r["updatedAt"] >= first
    assert len(_audit_lines(logs)) == 2


def test_toggle_to_the_same_value_logs_nothing(client, logs):
    assert call(client, "toggle", target="profile", enabled=True).status_code == 200
    assert _audit_lines(logs) == []
    call(client, "toggle", target="profile", enabled=False)
    call(client, "toggle", target="profile", enabled=False)
    assert len(_audit_lines(logs)) == 1


def test_unknown_field_ignored(client):
    call(client, "set_instructions", text="be brief")
    before = store.get_settings(USER_A)
    r = call(
        client,
        "set_instructions",
        text="be brief again",
        user_id="someone-else",
        revision=99,
        profile_revision=99,
        notice_pending=True,
    )
    assert r.status_code == 200
    after = store.get_settings(USER_A)
    assert after.instructions_text == "be brief again"
    assert after.profile_revision == before.profile_revision == 0
    assert after.notice_pending is False
    assert store.get_settings("someone-else").instructions_text == ""


def test_cross_user(client):
    call(client, "set_instructions", user=USER_A, text="only for a")
    b = call(client, "get", user=USER_B, personalizationSubject=USER_A)
    assert b.json()["instructions"]["text"] == ""
    call(client, "set_instructions", user=USER_B, text="only for b", personalizationSubject=USER_A)
    call(client, "clear_profile", user=USER_B, personalizationSubject=USER_A, userid=USER_A)
    assert store.get_settings(USER_A).instructions_text == "only for a"
    assert store.get_settings(USER_A).cleared_at is None
    assert store.get_settings(USER_B).instructions_text == "only for b"


def test_bad_tokens(client):
    expired = create_test_token(USER_A, SECRET, expires_at=timezone.now() - timedelta(hours=1))
    r = client.post(URL, {"userId": expired, "action": "get"}, format="json")
    assert r.status_code == 401
    tampered = token()[:-3] + ("AAA" if not token().endswith("AAA") else "BBB")
    r = client.post(URL, {"userId": tampered, "action": "get"}, format="json")
    assert r.status_code == 401
    r = client.post(URL, {"action": "get"}, format="json")
    assert r.status_code == 400


def test_token_in_body_only(client):
    r = client.post(URL, {"action": "get"}, format="json", HTTP_X_API_KEY=token())
    assert r.status_code == 400
    r = client.post(
        URL, {"userId": token(USER_B), "action": "get"}, format="json", HTTP_X_API_KEY=token()
    )
    call(client, "set_instructions", user=USER_B, text="b text")
    r = client.post(
        URL,
        {"userId": token(USER_B), "action": "get"},
        format="json",
        HTTP_X_API_KEY=token(USER_A),
    )
    assert r.json()["instructions"]["text"] == "b text"


def test_malformed_bodies(client):
    assert client.post(URL, {"userId": token()}, format="json").status_code == 400
    assert call(client, "nonsense").status_code == 400
    assert client.post(URL, [1, 2], format="json").status_code == 400
    assert client.post(URL, "not json", content_type="application/json").status_code == 400


def test_anonymous_404(client):
    r = client.post(
        URL, {"anonId": "6f1c3b2e-9a54-4c52-8d0a-3a1a2b3c4d5e", "action": "get"}, format="json"
    )
    assert r.status_code == 404
    assert r["Cache-Control"] == "no-store, private"


def test_anon_lab_origin_gone(client, lab, monkeypatch):
    monkeypatch.setenv("PERSONALIZATION_LAB_ORIGINS", "lab.example.test")
    r = client.post(
        URL,
        {"anonId": "6f1c3b2e-9a54-4c52-8d0a-3a1a2b3c4d5e", "action": "get"},
        format="json",
        HTTP_ORIGIN="https://lab.example.test",
    )
    assert r.status_code == 404
    from django.test import RequestFactory

    from chat.auth import AuthenticationRequired, authenticate_request

    request = RequestFactory().post("/", HTTP_ORIGIN="https://lab.example.test")
    with pytest.raises(AuthenticationRequired):
        authenticate_request(request, {"anonId": "6f1c3b2e-9a54-4c52-8d0a-3a1a2b3c4d5e"})


def test_gate_off_404(client, settings):
    settings.PERSONALIZATION_OVERRIDE_ENABLED = False
    r = call(client, "get")
    assert r.status_code == 404
    assert r["Cache-Control"] == "no-store, private"
    assert client.post(URL, {"action": "get"}, format="json").status_code == 404


def test_gate_off_without_allow_all_404(client, monkeypatch):
    monkeypatch.delenv("PERSONALIZATION_OVERRIDE_ALLOW_ALL")
    assert call(client, "get").status_code == 404


@pytest.mark.parametrize("environment", ["prod", "production", "beta", "", "PROD"])
def test_production_env_404(client, monkeypatch, environment):
    monkeypatch.setenv("ENVIRONMENT", environment)
    assert call(client, "get").status_code == 404
    assert call(client, "set_instructions", text="x").status_code == 404
    assert store.get_settings(USER_A).instructions_text == ""


@pytest.mark.parametrize(
    "action,fields",
    [
        ("run_now", {"kind": "session"}),
        ("set_testing", {"showPlainResponse": True}),
        ("health", {}),
    ],
)
def test_lab_only_actions_404_off_lab(client, action, fields):
    assert call(client, action, **fields).status_code == 404
    assert store.get_settings(USER_A).show_plain_response is False


def test_set_testing_on_lab(client, lab):
    r = call(client, "set_testing", showPlainResponse=True)
    assert r.status_code == 200
    assert r.json()["testing"] == {"showPlainResponse": True, "updateEveryMessage": False}
    r = call(client, "set_testing", updateEveryMessage=True)
    assert r.json()["testing"] == {"showPlainResponse": True, "updateEveryMessage": True}
    assert call(client, "set_testing", showPlainResponse="yes").status_code == 400


def test_clear_then_get(client):
    store.replace_sections(
        USER_A,
        0,
        [
            SectionData(
                key=None,
                kind="dynamic",
                heading="Made-up topic",
                body="Made-up body.",
                origin="inferred",
                last_supported_at=timezone.now(),
                position=0,
            )
        ],
        updated_by="la",
        processed_through=None,
        set_notice=True,
    )
    r = call(client, "clear_profile")
    assert r.status_code == 200
    profile = call(client, "get").json()["profile"]
    assert profile["text"] == "" and profile["sections"] == []
    assert profile["updatedAt"] is not None
    assert profile["updatedBy"] is None


def test_the_three_actions_are_live(client, lab, monkeypatch):
    """No placeholder answers any more: each action reaches its module (a fake model, no network)."""
    monkeypatch.setattr(
        profile_edit, "AnthropicLLMClient", lambda: FakeLLMClient(lambda s, u: "not json")
    )
    r = call(client, "edit_profile", request="more on parsha")
    assert r.status_code == 200 and r.json()["edit"]["status"] == "refused"
    r = call(client, "run_now", kind="session")
    assert r.status_code == 200 and r.json()["run"] == {
        "status": "no_change",
        "reason": "no_new_prompts",
    }
    r = call(client, "health")
    assert r.status_code == 200 and [j["name"] for j in r.json()["jobs"]] == ["session", "dream"]


def test_edit_profile_routes_and_validates(client, monkeypatch):
    seen = []

    def edit_profile(user_id, request, **_):
        seen.append((user_id, request))
        return "refused", "too vague"

    monkeypatch.setattr(profile_edit, "edit_profile", edit_profile)
    r = call(client, "edit_profile", request="make it better")
    assert r.status_code == 200
    assert r.json()["edit"] == {"status": "refused", "reason": "too vague"}
    assert "profile" in r.json()
    assert seen == [(USER_A, "make it better")]
    over = call(client, "edit_profile", request="a" * 301)
    assert over.status_code == 400 and over.json() == {"error": "too_long", "max": 300}
    assert call(client, "edit_profile").status_code == 400
    assert len(seen) == 1


def test_edit_profile_quota_429(client, monkeypatch):
    monkeypatch.setenv("PERSONALIZATION_EDIT_PER_HOUR", "1")
    monkeypatch.setattr(profile_edit, "edit_profile", lambda u, r, **_: ("applied", "ok"))
    assert call(client, "edit_profile", request="one").status_code == 200
    r = call(client, "edit_profile", request="two")
    assert r.status_code == 429 and r.json() == {"error": "quota"}
    assert call(client, "edit_profile", user=USER_B, request="two").status_code == 200


def test_run_now_routes_and_maps_results(client, lab, monkeypatch):
    results = iter(
        [
            types.SimpleNamespace(status="applied", reason=None, run_id=1),
            types.SimpleNamespace(status="busy", reason="running", run_id=None),
            types.SimpleNamespace(status="quota", reason=None, run_id=None),
        ]
    )
    calls = []

    def run_profile_job(user_id, trigger, **kw):
        calls.append((user_id, trigger, kw))
        return next(results)

    monkeypatch.setattr(profile_job, "run_profile_job", run_profile_job)
    r = call(client, "run_now", kind="dream")
    assert r.status_code == 200 and r.json()["run"] == {"status": "applied", "reason": None}
    assert calls[0][0] == USER_A and calls[0][1] == "run_now" and calls[0][2]["window"] == "dream"
    r = call(client, "run_now", kind="session")
    assert r.status_code == 200 and r.json()["run"]["status"] == "busy"
    assert calls[1][2]["window"] == "session"
    r = call(client, "run_now", kind="session")
    assert r.status_code == 429 and r.json() == {"error": "quota"}
    assert call(client, "run_now", kind="weekly").status_code == 400


def test_run_now_dream_runs_the_real_dream(client, lab, monkeypatch):
    from chat.V2.personalization.tests.test_profile_store import add_prompt

    add_prompt(USER_A, "a question", session="s1", at=timezone.now() - timedelta(days=1))
    seen = []

    def respond(system, user):
        seen.append(system)
        if system == load_chapter("profile_dream"):
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
        return "no"

    monkeypatch.setattr(profile_job, "AnthropicLLMClient", lambda: FakeLLMClient(respond))
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-not-real")
    r = call(client, "run_now", kind="dream")
    assert r.status_code == 200 and r.json()["run"]["status"] == "applied"
    assert r.json()["profile"]["sections"][0]["body"] == "You like short answers."
    assert ProfileWriterRun.objects.get().window == "dream"


def test_run_now_when_a_job_holds_the_lock_is_200_busy(client, lab):
    assert quota.acquire_job_lock(USER_A, timezone.now())
    r = call(client, "run_now", kind="session")
    assert r.status_code == 200 and r.json()["run"]["status"] == "busy"


def test_run_now_quota_is_429_through_the_real_job(client, lab, monkeypatch):
    from chat.V2.personalization.tests.test_profile_store import add_prompt

    add_prompt(USER_A, "a question", session="s1", at=timezone.now() - timedelta(days=1))
    monkeypatch.setenv("PERSONALIZATION_JOB_PER_HOUR", "0")
    monkeypatch.setattr(profile_job, "AnthropicLLMClient", lambda: FakeLLMClient(lambda s, u: ""))
    r = call(client, "run_now", kind="session")
    assert r.status_code == 429 and r.json() == {"error": "quota"}


def test_edit_profile_applies_through_the_real_module(client, monkeypatch):
    rev = store.get_settings(USER_A).profile_revision
    store.replace_sections(
        USER_A,
        rev,
        [
            SectionData(
                "current_interests",
                "core",
                "Current interests",
                "You study widely.",
                "inferred",
                timezone.now(),
                0,
            )
        ],
        updated_by="la",
        processed_through=None,
        set_notice=False,
    )

    def respond(system, user):
        if system == load_chapter("profile_edit"):
            return json.dumps(
                {
                    "status": "applied",
                    "reason": "Done.",
                    "sections": [
                        {
                            "key": "current_interests",
                            "heading": "",
                            "body": "You study only parsha.",
                        }
                    ],
                }
            )
        return "no"

    monkeypatch.setattr(profile_edit, "AnthropicLLMClient", lambda: FakeLLMClient(respond))
    r = call(client, "edit_profile", request="only in the context of parsha")
    assert r.status_code == 200 and r.json()["edit"]["status"] == "applied"
    assert r.json()["profile"]["updatedBy"] == "you_la"
    assert r.json()["profile"]["sections"][0]["body"] == "You study only parsha."


def test_health_routes(client, lab):
    store.record_run(user_id=USER_A, trigger="idle", window="dream", status="applied")
    r = call(client, "health")
    assert r.status_code == 200
    jobs = {j["name"]: j for j in r.json()["jobs"]}
    assert jobs["session"] == {
        "name": "session",
        "lastRunAt": None,
        "lastWriteAt": None,
        "parked": 0,
    }
    assert (
        jobs["dream"]["lastRunAt"] and jobs["dream"]["lastWriteAt"] and jobs["dream"]["parked"] == 0
    )
    assert r["Cache-Control"] == "no-store, private"


def test_token_never_logged(client, lab, logs):
    caplog = logs
    good = token()
    bad = token()[:-3] + "AAA"
    expired = create_test_token(USER_A, SECRET, expires_at=timezone.now() - timedelta(hours=1))
    with caplog.at_level(logging.DEBUG):
        for t in (good, bad, expired):
            client.post(URL, {"userId": t, "action": "get"}, format="json")
        client.post(URL, {"userId": good, "action": "set_instructions", "text": "t"}, format="json")
        client.post(
            URL,
            {"userId": good, "action": "toggle", "target": "profile", "enabled": 0},
            format="json",
        )
        client.post(URL, {"userId": good, "action": "clear_profile"}, format="json")
    assert caplog.records
    for t in (good, bad, expired):
        assert t not in caplog.text


def test_audit_line(client, logs):
    caplog = logs
    hashed = hashlib.sha256(USER_A.encode()).hexdigest()[:16]
    secret_text = "made-up private instruction text"
    with caplog.at_level(logging.INFO):
        call(client, "get")
        call(client, "set_instructions", text=secret_text)
        call(client, "toggle", target="profile", enabled=False)
        call(client, "clear_profile")
    lines = [r.getMessage() for r in caplog.records if "personalization_audit" in r.getMessage()]
    assert len(lines) == 3
    for action, line in zip(["set_instructions", "toggle", "clear_profile"], lines, strict=True):
        assert f"action={action}" in line and hashed in line
        assert USER_A not in line and secret_text not in line
    assert secret_text not in caplog.text


def test_a_busy_edit_returns_200_with_the_fixed_reason_and_the_current_state(client, monkeypatch):
    rev = store.get_settings(USER_A).profile_revision
    sections = [
        SectionData(
            "current_interests",
            "core",
            "Current interests",
            "You study widely.",
            "inferred",
            timezone.now(),
            0,
        )
    ]
    store.replace_sections(
        USER_A, rev, sections, updated_by="la", processed_through=None, set_notice=False
    )
    edit_system = load_chapter("profile_edit")

    def respond(system, user):
        if system != edit_system:
            return "no"
        # a Profile job's write lands during every attempt
        cur = store.get_settings(USER_A).profile_revision
        store.replace_sections(
            USER_A, cur, sections, updated_by="la", processed_through=None, set_notice=False
        )
        return json.dumps(
            {
                "status": "applied",
                "reason": "x",
                "sections": [{"key": "current_interests", "heading": "", "body": "Only parsha."}],
            }
        )

    monkeypatch.setattr(profile_edit, "AnthropicLLMClient", lambda: FakeLLMClient(respond))
    r = call(client, "edit_profile", request="only parsha")
    assert r.status_code == 200
    assert r.json()["edit"] == {
        "status": "refused",
        "reason": "Your Profile was being updated. Try again in a moment.",
    }
    assert r.json()["profile"]["sections"][0]["body"] == "You study widely."
    assert r.json()["profile"]["updatedBy"] == "la"
    assert ProfileWriterRun.objects.filter(trigger="edit", reason="busy").count() == 1
