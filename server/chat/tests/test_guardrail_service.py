"""Tests for GuardrailService — pre-agent LLM message filter."""

from unittest.mock import MagicMock

from chat.V2.agent.claude_service import _sum_costs
from chat.V2.guardrail.guardrail_service import GuardrailService, parse_guardrail_response


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
            '{"decision": "ALLOW", "reason": ""}'
        )
        result = service.check_message("What is Shabbat?")
        assert result.allowed is True
        assert result.reason == ""

    def test_blocked_message(self):
        service = self._make_service()
        service.client.messages.create.return_value = _make_anthropic_response(
            '{"decision": "BLOCK", "reason": "Off-topic question"}'
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

    def test_missing_decision_field_fails_closed(self):
        service = self._make_service()
        service.client.messages.create.return_value = _make_anthropic_response(
            '{"reason": "some reason"}'
        )
        result = service.check_message("Hello")
        assert result.allowed is False

    def test_no_api_key_raises(self):
        """Missing API key is a config error — should raise, not silently degrade."""
        from unittest.mock import patch

        import pytest

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            with pytest.raises(ValueError):
                GuardrailService()

    def test_decision_format_allow(self):
        """Prompt returns {decision: "ALLOW", reason: "..."} format."""
        service = self._make_service()
        service.client.messages.create.return_value = _make_anthropic_response(
            '{"decision": "ALLOW", "reason": "Legitimate question about Jewish texts"}'
        )
        result = service.check_message("What is Shabbat?")
        assert result.allowed is True
        assert result.reason == "Legitimate question about Jewish texts"

    def test_decision_format_block(self):
        """Prompt returns {decision: "BLOCK", reason: "..."} format."""
        service = self._make_service()
        service.client.messages.create.return_value = _make_anthropic_response(
            '{"decision": "BLOCK", "reason": "Not about Jewish texts"}'
        )
        result = service.check_message("How do I hack a website?")
        assert result.allowed is False
        assert result.reason == "Not about Jewish texts"

    def test_decision_format_with_code_fences(self):
        """Handles response wrapped in markdown code fences."""
        service = self._make_service()
        service.client.messages.create.return_value = _make_anthropic_response(
            '```json\n{"decision": "ALLOW", "reason": "On-topic question"}\n```'
        )
        result = service.check_message("What is Shabbat?")
        assert result.allowed is True
        assert result.reason == "On-topic question"


class TestParseGuardrailResponse:
    """Tests for the standalone parse_guardrail_response() function."""

    def test_allow(self):
        result = parse_guardrail_response('{"decision": "ALLOW", "reason": "On topic"}')
        assert result.allowed is True
        assert result.reason == "On topic"

    def test_block(self):
        result = parse_guardrail_response('{"decision": "BLOCK", "reason": "Off topic"}')
        assert result.allowed is False
        assert result.reason == "Off topic"

    def test_case_insensitive_allow(self):
        result = parse_guardrail_response('{"decision": "allow", "reason": ""}')
        assert result.allowed is True

    def test_code_fences(self):
        result = parse_guardrail_response('```json\n{"decision": "ALLOW", "reason": ""}\n```')
        assert result.allowed is True

    def test_malformed_json_fails_closed(self):
        result = parse_guardrail_response("not json at all")
        assert result.allowed is False
        assert "malformed" in result.reason.lower()

    def test_missing_decision_fails_closed(self):
        result = parse_guardrail_response('{"reason": "some reason"}')
        assert result.allowed is False

    def test_empty_string_fails_closed(self):
        result = parse_guardrail_response("")
        assert result.allowed is False

    def test_unknown_decision_fails_closed(self):
        result = parse_guardrail_response('{"decision": "MAYBE", "reason": ""}')
        assert result.allowed is False


class TestSumCosts:
    """Tests for _sum_costs helper."""

    def test_both_present(self):
        assert _sum_costs(0.01, 0.02) == 0.03

    def test_one_none(self):
        assert _sum_costs(None, 0.05) == 0.05
        assert _sum_costs(0.05, None) == 0.05

    def test_both_none(self):
        assert _sum_costs(None, None) is None

    def test_zero_values(self):
        assert _sum_costs(0.0, 0.0) == 0.0
