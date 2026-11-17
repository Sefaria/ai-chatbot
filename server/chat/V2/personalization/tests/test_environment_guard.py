"""Memory stays off production deployments, a global mode warns outside production, and the
worker thread closes its database connections."""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from chat.models import ChatSession
from chat.tests.test_streaming_integration import create_test_token  # noqa: F401
from chat.V2.personalization import gate, runtime
from chat.V2.personalization.tests.conftest import NOW, make_turn

SECRET = "guard-test-secret"
TOKEN = create_test_token("guard-user", SECRET)


def make_settings(mode="off", enabled=False):
    return SimpleNamespace(PERSONALIZATION_MODE=mode, PERSONALIZATION_OVERRIDE_ENABLED=enabled)


@pytest.mark.parametrize("environment", ["prod", "production", "beta", "", None])
def test_override_flag_alone_enables_no_memory_on_production(monkeypatch, environment):
    if environment is None:
        monkeypatch.delenv("ENVIRONMENT", raising=False)
    else:
        monkeypatch.setenv("ENVIRONMENT", environment)
    assert gate.memory_enabled(make_settings(enabled=True)) is False


def test_override_flag_enables_memory_on_a_preview(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "preview")
    assert gate.memory_enabled(make_settings(enabled=True)) is True
    assert gate.memory_enabled(make_settings(enabled=False)) is False


def test_setting_mode_keeps_memory_on_whatever_the_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "prod")
    assert gate.memory_enabled(make_settings(mode="explicit")) is True


@pytest.mark.django_db
@override_settings(
    CHATBOT_USER_TOKEN_SECRET=SECRET,
    PERSONALIZATION_OVERRIDE_ENABLED=True,
    PERSONALIZATION_MODE="off",
)
@pytest.mark.parametrize("environment, moves", [("prod", 0), ("beta", 0), ("preview", 1)])
def test_claim_calls_move_only_off_production(monkeypatch, environment, moves):
    from chat.auth.anonymous import anonymous_user_id

    monkeypatch.setenv("ENVIRONMENT", environment)
    anon_id = uuid.uuid4()
    ChatSession.objects.create(session_id="s", user_id=anonymous_user_id(anon_id))
    with patch.object(runtime, "move_profile") as move:
        response = APIClient().post(
            "/api/v2/anon/claim", {"userId": TOKEN, "anonId": str(anon_id)}, format="json"
        )
    assert response.status_code == 200
    assert move.call_count == moves


@pytest.mark.parametrize(
    "mode, environment, warns",
    [
        ("realtime", "preview", True),
        ("hybrid", "", True),
        ("realtime", "prod", False),
        ("realtime", "beta", False),
        ("off", "preview", False),
    ],
)
def test_startup_warning_for_a_global_mode_outside_production(
    monkeypatch, mode, environment, warns
):
    monkeypatch.setenv("ENVIRONMENT", environment)
    with patch.object(gate, "logger") as log:
        gate.warn_if_mode_set_outside_production(make_settings(mode=mode))
    assert log.warning.called is warns
    if warns:
        assert "PR preview" in log.warning.call_args.args[0]


def test_worker_closes_all_connections_when_the_job_ends():
    config = SimpleNamespace(mode="realtime")
    service = MagicMock()
    service.after_turn.side_effect = RuntimeError("boom")
    with (
        patch.object(runtime, "get_service", return_value=service),
        patch.object(runtime, "capture_exception"),
        patch.object(runtime, "close_old_connections"),
        patch.object(runtime.connections, "close_all") as close_all,
    ):
        runtime._user_lock("u1")
        runtime._run_after_turn(config, make_turn("hi", user="u1"), NOW, None, MagicMock())
    close_all.assert_called_once()
