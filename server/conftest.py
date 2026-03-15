"""Pytest configuration for Django tests."""

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "chatbot_server.test_settings")


@pytest.fixture(autouse=True)
def _mock_guardrail_allow_all():
    """Auto-mock the guardrail service to allow all messages in tests.

    Without this, every test that exercises the agent path would need a
    real Anthropic API key and Braintrust prompt — and would fail closed
    (blocking all messages) if either is unavailable.

    Tests that specifically test guardrail behavior can override by
    patching get_guardrail_service themselves.
    """
    from chat.V2.guardrail.guardrail_service import GuardrailResult

    mock_service = MagicMock()
    mock_service.check_message.return_value = GuardrailResult(allowed=True)

    with patch("chat.V2.agent.guardrail_gate.get_guardrail_service", return_value=mock_service):
        yield mock_service
