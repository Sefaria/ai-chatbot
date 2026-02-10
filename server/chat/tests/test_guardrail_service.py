"""Tests for GuardrailService — pre-agent LLM message filter."""

from unittest.mock import MagicMock

from chat.V2.guardrail.guardrail_service import GuardrailService


def _make_anthropic_response(text: str):
    """Build a mock Anthropic Messages response."""
    block = MagicMock()
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


class TestGuardrailService:
    """Test GuardrailService.check_message() with mocked Anthropic client.

    Uses __new__ to bypass __init__ (avoids needing real API keys / Braintrust).
    Verifies the "fail closed" contract: every error path must block, never allow.
    """

    def _make_service(self):
        service = GuardrailService.__new__(GuardrailService)
        service.client = MagicMock()
        service.prompt_service = MagicMock()
        service.prompt_service.get_core_prompt.return_value = MagicMock(text="You are a guardrail.")
        return service

    def test_allowed_message(self):
        service = self._make_service()
        service.client.messages.create.return_value = _make_anthropic_response(
            '{"allowed": true, "reason": ""}'
        )
        result = service.check_message("What is Shabbat?")
        assert result.allowed is True
        assert result.reason == ""

    def test_blocked_message(self):
        service = self._make_service()
        service.client.messages.create.return_value = _make_anthropic_response(
            '{"allowed": false, "reason": "Off-topic question"}'
        )
        result = service.check_message("What is the capital of France?")
        assert result.allowed is False
        assert result.reason == "Off-topic question"

    def test_braintrust_down_fails_closed(self):
        service = self._make_service()
        service.prompt_service.get_core_prompt.side_effect = RuntimeError("Braintrust unavailable")
        result = service.check_message("Hello")
        assert result.allowed is False
        assert "unavailable" in result.reason.lower()

    def test_llm_error_fails_closed(self):
        service = self._make_service()
        service.client.messages.create.side_effect = Exception("API error")
        result = service.check_message("Hello")
        assert result.allowed is False
        assert "unavailable" in result.reason.lower()

    def test_malformed_json_fails_closed(self):
        service = self._make_service()
        service.client.messages.create.return_value = _make_anthropic_response(
            "This is not JSON at all"
        )
        result = service.check_message("Hello")
        assert result.allowed is False
        assert "malformed" in result.reason.lower()

    def test_missing_allowed_field_fails_closed(self):
        service = self._make_service()
        service.client.messages.create.return_value = _make_anthropic_response(
            '{"reason": "some reason"}'
        )
        result = service.check_message("Hello")
        assert result.allowed is False

    def test_no_client_fails_closed(self):
        service = GuardrailService.__new__(GuardrailService)
        service.client = None
        service.prompt_service = MagicMock()
        result = service.check_message("Hello")
        assert result.allowed is False

    def test_braintrust_decision_format_allow(self):
        """Braintrust prompt returns {decision: "ALLOW", ...} format."""
        service = self._make_service()
        service.client.messages.create.return_value = _make_anthropic_response(
            '```json\n{"decision": "ALLOW", "reason_codes": [], "refusal_message": null, "confidence": 0.99}\n```'
        )
        result = service.check_message("What is Shabbat?")
        assert result.allowed is True
        assert result.reason == ""

    def test_braintrust_decision_format_block(self):
        """Braintrust prompt returns {decision: "BLOCK", ...} format."""
        service = self._make_service()
        service.client.messages.create.return_value = _make_anthropic_response(
            '```json\n{"decision": "BLOCK", "reason_codes": ["OFF_TOPIC"], "refusal_message": "Not about Jewish texts", "confidence": 0.95}\n```'
        )
        result = service.check_message("How do I hack a website?")
        assert result.allowed is False
        assert result.reason == "Not about Jewish texts"
