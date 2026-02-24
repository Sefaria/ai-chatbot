"""Pytest configuration for Django tests."""

import os
from unittest.mock import patch

import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "chatbot_server.test_settings")


@pytest.fixture(autouse=True)
def _mock_guardrail_allow_all():
    """Auto-mock the guardrail parser to allow all messages in tests.

    Without this, every test that exercises the agent path would need a
    real Anthropic API key and Braintrust prompt — and would fail closed
    (blocking all messages) if either is unavailable.

    Tests that specifically test guardrail behavior can override by
    patching parse_guardrail_response themselves.
    """
    from chat.V2.guardrail.guardrail_service import GuardrailResult

    with patch(
        "chat.V2.agent.claude_service.parse_guardrail_response",
        return_value=GuardrailResult(allowed=True),
    ):
        yield
