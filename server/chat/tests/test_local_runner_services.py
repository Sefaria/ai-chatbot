"""The runner's replacements for the services that need Braintrust.

Each proxy must return exactly what the service it replaces returns, so the turn
code above it cannot tell the difference — that is what keeps local mode at
parity rather than merely similar. These also pin the failure policies, which
differ per service on purpose.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chat.V2.router.router_service import RouteType
from local_runner import services

POST = "local_runner.services._post"


class TestPromptProxy:
    def test_returns_a_core_prompt(self):
        payload = {"text": "PROMPT BODY", "promptId": "core-8fbc", "version": "7"}
        with patch(POST, return_value=payload) as post:
            prompt = services.ProxyPromptService().get_core_prompt(prompt_id="core-8fbc")

        assert prompt.text == "PROMPT BODY"
        assert prompt.prompt_id == "core-8fbc"
        assert prompt.version == "7"
        assert post.call_args.args[0] == "/api/v2/local/prompt"

    def test_forwards_build_vars(self):
        with patch(POST, return_value={"text": "x"}) as post:
            services.ProxyPromptService().get_core_prompt(
                prompt_id="core", build_vars={"response_format": "RF"}
            )

        assert post.call_args.args[1]["buildVars"] == {"response_format": "RF"}


class TestGuardrailProxy:
    def test_passes_through_an_allow(self):
        with patch(POST, return_value={"allowed": True, "reason": "fine"}):
            result = services.ProxyGuardrailService().check_message("hello")

        assert result.allowed is True
        assert result.reason == "fine"

    def test_passes_through_a_block(self):
        with patch(POST, return_value={"allowed": False, "reason": "nope"}):
            result = services.ProxyGuardrailService().check_message("hello")

        assert result.allowed is False
        assert result.reason == "nope"

    def test_fails_closed_when_the_server_is_unreachable(self):
        with patch(POST, side_effect=services.ProxyError("down")):
            result = services.ProxyGuardrailService().check_message("hello")

        # An unchecked message is not an allowed one: the guardrail is a
        # brand-safety control, so losing it must not mean losing enforcement.
        assert result.allowed is False
        assert result.reason == services.GUARDRAIL_UNAVAILABLE


class TestRouterProxy:
    def test_maps_the_route(self):
        payload = {
            "route": RouteType.DISCOVERY.value,
            "corePromptId": "core-x",
            "rewrittenMessage": "rewritten",
        }
        with patch(POST, return_value=payload):
            result = services.ProxyRouterService().classify("hello")

        assert result.route == RouteType.DISCOVERY
        assert result.core_prompt_id == "core-x"
        assert result.rewritten_message == "rewritten"

    def test_fails_open_to_discovery(self):
        with patch(POST, side_effect=services.ProxyError("down")):
            result = services.ProxyRouterService().classify("hello")

        # Matches RouterService's own behaviour on error: routing is an
        # optimisation, so losing it should not lose the turn.
        assert result.route == RouteType.DISCOVERY

    def test_unknown_route_falls_back_to_discovery(self):
        with patch(POST, return_value={"route": "not-a-route"}):
            result = services.ProxyRouterService().classify("hello")

        assert result.route == RouteType.DISCOVERY


class TestSummaryProxy:
    """The agent receives only the current message, so the summary is the whole
    multi-turn memory. Losing it silently would make local mode single-turn."""

    def test_uses_the_llm_path(self):
        assert services.ProxySummaryService().use_llm is True

    def test_builds_no_anthropic_client(self):
        # The real service constructs one in __init__, which needs a key.
        assert services.ProxySummaryService().client is None

    def test_applies_the_returned_summary_locally(self):
        service = services.ProxySummaryService()
        service._apply_summary_data = MagicMock(return_value="applied")
        summary = {"text": "so far", "current_topic": "Genesis"}

        with patch(POST, return_value={"summary": summary}) as post:
            result = service._llm_summarize(MagicMock(), None, "user msg", "assistant msg")

        assert result == "applied"
        assert post.call_args.args[0] == "/api/v2/local/summary"
        # The row is written here, not on the server: local history stays local.
        assert service._apply_summary_data.call_args.kwargs["data"] == summary

    def test_sends_the_previous_summary_as_context(self):
        service = services.ProxySummaryService()
        service._apply_summary_data = MagicMock()
        current = MagicMock()
        current.to_prompt_text.return_value = "Previous: Genesis"

        with patch(POST, return_value={"summary": {}}) as post:
            service._llm_summarize(MagicMock(), current, "u", "a")

        assert post.call_args.args[1]["previousSummary"] == "Previous: Genesis"

    def test_falls_back_to_rule_based_when_unreachable(self):
        service = services.ProxySummaryService()
        service._simple_summarize = MagicMock(return_value="simple")

        with patch(POST, side_effect=services.ProxyError("down")):
            result = service._llm_summarize(MagicMock(), None, "u", "a")

        assert result == "simple"

    def test_falls_back_when_the_response_has_no_summary(self):
        service = services.ProxySummaryService()
        service._simple_summarize = MagicMock(return_value="simple")

        with patch(POST, return_value={}):
            result = service._llm_summarize(MagicMock(), None, "u", "a")

        assert result == "simple"


class TestAppetizerIsDisabled:
    async def test_returns_nothing(self):
        assert await services.NullAppetizerService().find_appetizer("hello") is None

    async def test_records_why_in_the_metrics_sink(self):
        sink = {}
        await services.NullAppetizerService().find_appetizer("hello", metrics_sink=sink)
        assert sink["source"] == "disabled_local_runner"


class TestPostRequiresPairing:
    def test_unpaired_runner_cannot_call_the_server(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SEFARIA_AGENT_HOME", str(tmp_path))
        with pytest.raises(services.ProxyError, match="not paired"):
            services._post("/api/v2/local/prompt", {})
