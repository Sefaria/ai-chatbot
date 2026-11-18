"""Fixtures for the harness tests. `evals/` is outside pytest's testpaths, so the autouse
guardrail mock from `server/conftest.py` is repeated here. Run from `server/` (see README)."""

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "chatbot_server.test_settings")


@pytest.fixture(autouse=True)
def _mock_guardrail_allow_all():
    from chat.V2.guardrail.guardrail_service import GuardrailResult

    mock_service = MagicMock()
    mock_service.check_message.return_value = GuardrailResult(allowed=True)
    with patch(
        "chat.V2.agent.guardrail_gate.get_guardrail_service", return_value=mock_service
    ):
        yield mock_service


@pytest.fixture(autouse=True)
def _personalization_env(monkeypatch, settings):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-not-real")
    settings.PERSONALIZATION_JEV_GATE = "off"
