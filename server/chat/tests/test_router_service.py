"""Tests for RouterService — post-guardrail message classifier."""

from unittest.mock import MagicMock

from chat.V2.router.router_service import RouterService, RouteType


def _make_anthropic_response(text: str):
    """Build a mock Anthropic Messages response."""
    block = MagicMock()
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


class TestRouterService:
    """Test RouterService.classify() with mocked Anthropic client.

    Uses __new__ to bypass __init__ (avoids needing real API keys / Braintrust).
    Verifies the "fail open" contract: every error path must default to Discovery.
    """

    def _make_service(self):
        service = RouterService.__new__(RouterService)
        service.client = MagicMock()
        service.prompt_service = MagicMock()
        service.prompt_service.get_core_prompt.return_value = MagicMock(text="You are a router.")
        return service

    def test_translation_route(self):
        service = self._make_service()
        service.client.messages.create.return_value = _make_anthropic_response(
            '{"route": "translation", "reason": "User wants a translation"}'
        )
        result = service.classify("Translate Genesis 1:1")
        assert result.route == RouteType.TRANSLATION
        assert result.core_prompt_id == "Translation"
        assert result.rewritten_message is None

    def test_discovery_route(self):
        service = self._make_service()
        service.client.messages.create.return_value = _make_anthropic_response(
            '{"route": "discovery", "reason": "User asking about Jewish texts"}'
        )
        result = service.classify("What is Shabbat?")
        assert result.route == RouteType.DISCOVERY
        assert result.core_prompt_id is None
        assert result.rewritten_message is None

    def test_other_route_triggers_rewrite(self):
        service = self._make_service()
        # First call: classification returns "other"
        # Second call: rewriter returns rewritten question
        service.client.messages.create.side_effect = [
            _make_anthropic_response('{"route": "other", "reason": "Vague message"}'),
            _make_anthropic_response("What aspects of Jewish philosophy relate to your question?"),
        ]
        result = service.classify("I'm feeling lost")
        assert result.route == RouteType.OTHER
        assert result.core_prompt_id is None
        assert (
            result.rewritten_message == "What aspects of Jewish philosophy relate to your question?"
        )

    def test_braintrust_down_fails_open(self):
        service = self._make_service()
        service.prompt_service.get_core_prompt.side_effect = RuntimeError("Braintrust unavailable")
        result = service.classify("Hello")
        assert result.route == RouteType.DISCOVERY
        assert result.core_prompt_id is None

    def test_llm_error_fails_open(self):
        service = self._make_service()
        service.client.messages.create.side_effect = Exception("API error")
        result = service.classify("Hello")
        assert result.route == RouteType.DISCOVERY
        assert result.core_prompt_id is None

    def test_malformed_json_fails_open(self):
        service = self._make_service()
        service.client.messages.create.return_value = _make_anthropic_response(
            "This is not JSON at all"
        )
        result = service.classify("Hello")
        assert result.route == RouteType.DISCOVERY
        assert result.core_prompt_id is None

    def test_rewriter_failure_still_returns_other(self):
        service = self._make_service()
        # Classification succeeds, rewriter fails
        service.client.messages.create.side_effect = [
            _make_anthropic_response('{"route": "other", "reason": "Vague"}'),
            Exception("Rewriter API error"),
        ]
        result = service.classify("hmm")
        assert result.route == RouteType.OTHER
        assert result.rewritten_message is None

    def test_markdown_code_fences_handled(self):
        service = self._make_service()
        service.client.messages.create.return_value = _make_anthropic_response(
            '```json\n{"route": "translation", "reason": "Translation request"}\n```'
        )
        result = service.classify("Translate this verse")
        assert result.route == RouteType.TRANSLATION
        assert result.core_prompt_id == "Translation"

    def test_missing_route_field_fails_open(self):
        service = self._make_service()
        service.client.messages.create.return_value = _make_anthropic_response(
            '{"reason": "some reason"}'
        )
        result = service.classify("Hello")
        assert result.route == RouteType.DISCOVERY

    def test_unknown_route_value_fails_open(self):
        service = self._make_service()
        service.client.messages.create.return_value = _make_anthropic_response(
            '{"route": "unknown_route", "reason": "something"}'
        )
        result = service.classify("Hello")
        assert result.route == RouteType.DISCOVERY

    def test_case_insensitive_route(self):
        service = self._make_service()
        service.client.messages.create.return_value = _make_anthropic_response(
            '{"route": "TRANSLATION", "reason": "Uppercase route"}'
        )
        result = service.classify("Translate this")
        assert result.route == RouteType.TRANSLATION

    def test_no_api_key_raises(self):
        """Missing API key is a config error — should raise, not silently degrade."""
        from unittest.mock import patch

        import pytest

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            with pytest.raises(ValueError):
                RouterService()
