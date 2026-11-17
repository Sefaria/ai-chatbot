"""The status and profile endpoints used to test the feature on a preview."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from django.db import connection
from django.test import override_settings
from rest_framework.test import APIClient

from chat.models import ChatSession, ProfileSlot  # noqa: F401
from chat.tests.test_streaming_integration import create_test_token
from chat.user_token_service import decrypt_chatbot_user_identity
from chat.V2.personalization import runtime
from chat.V2.personalization.store import DjangoProfileStore
from chat.V2.personalization.strategies.base import BatchResult

SECRET = "api-test-secret"
TOKEN = create_test_token("api-user", SECRET)
USER_ID = decrypt_chatbot_user_identity(TOKEN, SECRET).user_id
OTHER_TOKEN = create_test_token("someone-else", SECRET)
OTHER_ID = decrypt_chatbot_user_identity(OTHER_TOKEN, SECRET).user_id

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "preview")


@pytest.fixture
def honoured():
    with override_settings(
        CHATBOT_USER_TOKEN_SECRET=SECRET,
        PERSONALIZATION_OVERRIDE_ENABLED=True,
        PERSONALIZATION_OVERRIDE_USER_IDS=USER_ID,
    ):
        yield


def post(path, body, token=TOKEN):
    return APIClient().post(
        f"/api/v2/personalization/{path}", {"userId": token, **body}, format="json"
    )


def profile(action, token=TOKEN, **body):
    return post("profile", {"action": action, **body}, token)


def slots_of(response):
    return {s["slot"]: s for s in response.data["slots"]}


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_status_reports_missing_tables():
    with connection.cursor() as cursor:
        cursor.execute(f"ALTER TABLE {ProfileSlot._meta.db_table} RENAME TO moved_away")
    response = post("status", {})
    assert response.status_code == 200
    assert response.data["tables"] is False


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_status_returns_only_callers_id():
    response = post("status", {})
    assert response.status_code == 200
    assert response.data["persistentUserId"] == USER_ID
    assert response.data["tables"] is True
    assert response.data["mode"] == "off" and response.data["overrideAllowedForYou"] is False
    assert response.data["environment"] == "preview"
    assert OTHER_ID not in str(response.data)
    other = post("status", {}, OTHER_TOKEN)
    assert other.data["persistentUserId"] == OTHER_ID and USER_ID not in str(other.data)
    assert post("status", {"userId": "garbage"}).status_code == 401


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET, PERSONALIZATION_OVERRIDE_ENABLED=True)
def test_profile_api_is_404_without_allowlist():
    with override_settings(PERSONALIZATION_OVERRIDE_USER_IDS=""):
        assert profile("get").status_code == 404
    with override_settings(PERSONALIZATION_OVERRIDE_USER_IDS=OTHER_ID):
        assert profile("get").status_code == 404
    with override_settings(
        PERSONALIZATION_OVERRIDE_USER_IDS=USER_ID, PERSONALIZATION_MODE="explicit"
    ):
        assert profile("get").status_code == 200
    with override_settings(
        PERSONALIZATION_OVERRIDE_USER_IDS=USER_ID, PERSONALIZATION_OVERRIDE_ENABLED=False
    ):
        assert profile("get").status_code == 404


@pytest.mark.parametrize("environment", ["prod", "production", "beta", ""])
def test_profile_api_is_404_in_production_environment(honoured, monkeypatch, environment):
    monkeypatch.setenv("ENVIRONMENT", environment)
    assert profile("get").status_code == 404
    assert profile("clear").status_code == 404


def test_set_get_release_clear_roundtrip(honoured):
    response = profile("set", slot="preferred_language", value="he")
    assert response.status_code == 200 and response.data["applied"] is True
    got = profile("get")
    slot = slots_of(got)["preferred_language"]
    assert slot["value"] == "he" and slot["status"] == "stated" and slot["evidence"] == []
    assert got.data["revision"] >= 1 and got.data["storeUserId"] == USER_ID
    assert got.data["settings"] == {"personalizeEnabled": True, "storeEnabled": True}

    assert profile("release", slot="preferred_language").status_code == 200
    assert slots_of(profile("get"))["preferred_language"]["status"] == "inferred"

    assert profile("clear").data["applied"] is True
    assert profile("get").data["slots"] == []

    assert profile("set", slot="no_such_slot", value="x").status_code == 400
    assert profile("set", slot="preferred_language", value="klingon").status_code == 400


def test_set_screens_sensitive_values(honoured):
    registry = runtime.get_registry()
    text_slots = [s.key for s in registry.all() if s.value_type == "text"]
    assert text_slots
    with patch.object(runtime.get_service("explicit").screen, "is_sensitive", return_value=True):
        response = profile("set", slot=text_slots[0], value="anything")
    assert response.data["applied"] is False and response.data["reason"] == "sensitive"


def test_settings_toggle_stops_storing(honoured):
    assert profile("settings", storeEnabled=False).data["applied"] is True
    assert profile("get").data["settings"]["storeEnabled"] is False
    refused = profile("set", slot="preferred_language", value="he")
    assert refused.data["applied"] is False and refused.data["reason"] == "store_disabled"
    assert profile("settings", storeEnabled=True, personalizeEnabled=False).status_code == 200
    assert profile("get").data["settings"] == {"personalizeEnabled": False, "storeEnabled": True}
    assert profile("settings").status_code == 400


def test_resolve_proposal_accept_and_reject(honoured):
    store = DjangoProfileStore()
    now = datetime.now(UTC)
    ids = []
    registry = runtime.get_registry()
    keys = [s.key for s in registry.all() if s.value_type == "enum"][:2]
    for key in keys:
        value = registry.get(key).allowed[0]
        result = store.add_proposal(USER_ID, key, value, "quote", now)
        assert result.applied, result.reason
        ids.append(result.proposal.id)
    listed = profile("get").data["proposals"]
    assert {p["id"] for p in listed} == set(ids) and all(p["status"] == "pending" for p in listed)

    assert profile("resolve_proposal", proposalId=ids[0], accept=True).data["applied"] is True
    assert profile("resolve_proposal", proposalId=ids[1], accept=False).data["applied"] is True
    statuses = {p["id"]: p["status"] for p in profile("get").data["proposals"]}
    assert statuses == {ids[0]: "accepted", ids[1]: "rejected"}
    assert keys[0] in slots_of(profile("get"))
    assert profile("resolve_proposal", proposalId=ids[0]).status_code == 400


def test_run_batch_only_touches_callers_user(honoured):
    strategy = MagicMock()
    strategy.run_batch.return_value = BatchResult(users_processed=1, llm_calls=2)
    service = MagicMock(strategy=strategy, mode="nightly")
    with patch.object(runtime, "get_service", return_value=service) as get_service:
        response = profile("run_batch", mode="nightly")
    assert response.status_code == 200 and response.data["usersProcessed"] == 1
    get_service.assert_called_once_with("nightly")
    (_now, user_ids), _ = strategy.run_batch.call_args
    assert user_ids == [USER_ID]

    with patch.object(runtime, "get_service", return_value=None):
        assert profile("run_batch", mode="nightly").status_code == 400
    assert profile("run_batch").status_code == 400  # the server mode is off


def test_subject_scopes_the_profile(honoured):
    profile("set", slot="preferred_language", value="he")
    scoped = profile("set", slot="preferred_language", value="en", personalizationSubject="eval-1")
    assert scoped.status_code == 200
    got = profile("get", personalizationSubject="eval-1")
    assert got.data["storeUserId"] == f"{USER_ID}:eval:eval-1"
    assert slots_of(got)["preferred_language"]["value"] == "en"
    assert slots_of(profile("get"))["preferred_language"]["value"] == "he"
    profile("clear", personalizationSubject="eval-1")
    assert profile("get", personalizationSubject="eval-1").data["slots"] == []
    assert slots_of(profile("get"))["preferred_language"]["value"] == "he"
    assert profile("get", personalizationSubject="Bad Subject!").status_code == 400
