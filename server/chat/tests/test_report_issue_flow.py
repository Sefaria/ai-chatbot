"""Tests for the client-initiated `report_issue` flow.

The flow's whole mechanism is: when the client declares a flow, its prompt wins
and the router never runs. These tests pin that behaviour down, plus the
normalisation that keeps a malformed client from disabling routing.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.test import override_settings

from chat.V2.agent.contracts import ConversationMessage, MessageContext
from chat.V2.agent.flows import REPORT_ISSUE, get_flow_prompt_slug, normalize_flow
from chat.V2.agent.turn_orchestrator import TurnOrchestrator


class TestNormalizeFlow:
    def test_known_flow_passes_through(self):
        assert normalize_flow("report_issue") == REPORT_ISSUE

    def test_surrounding_whitespace_is_stripped(self):
        assert normalize_flow("  report_issue  ") == REPORT_ISSUE

    @pytest.mark.parametrize(
        "raw",
        [None, "", "   ", "unknown_flow", 17, {"flow": "report_issue"}, ["report_issue"], True],
    )
    def test_unrecognised_values_fail_open_to_no_flow(self, raw):
        """Anything we don't recognise must leave the router in charge."""
        assert normalize_flow(raw) is None

    def test_flow_names_are_case_sensitive(self):
        assert normalize_flow("Report_Issue") is None


class TestGetFlowPromptSlug:
    @override_settings(REPORT_ISSUE_PROMPT_SLUG="report-issue")
    def test_returns_configured_slug(self):
        assert get_flow_prompt_slug(REPORT_ISSUE) == "report-issue"

    @override_settings(REPORT_ISSUE_PROMPT_SLUG="report-issue-sandbox")
    def test_slug_is_read_from_settings_at_call_time(self):
        assert get_flow_prompt_slug(REPORT_ISSUE) == "report-issue-sandbox"

    def test_no_flow_has_no_slug(self):
        assert get_flow_prompt_slug(None) is None
        assert get_flow_prompt_slug("") is None

    def test_unknown_flow_has_no_slug(self):
        assert get_flow_prompt_slug("nonsense") is None

    @override_settings(REPORT_ISSUE_PROMPT_SLUG="")
    def test_blank_configured_slug_is_treated_as_unset(self):
        """Better to fall back to the router than to request an empty prompt id."""
        assert get_flow_prompt_slug(REPORT_ISSUE) is None


def build_orchestrator(router_result=(None, "discovery", None)):
    """A TurnOrchestrator whose collaborators are all mocks.

    Returns (orchestrator, mocks) so tests can assert on what was called.
    """
    prompt_service = MagicMock()
    prompt_service.get_core_prompt.side_effect = lambda prompt_id, build_vars=None: SimpleNamespace(
        text=f"PROMPT[{prompt_id}]", prompt_id=prompt_id, version="1"
    )

    router = MagicMock()

    async def _run_router(bt_span, user_message, messages):
        override, route, replacement = router_result
        return override, route, (replacement if replacement is not None else messages)

    router.run_router = AsyncMock(side_effect=_run_router)

    guardrail_gate = MagicMock()
    guardrail_gate.run_guardrail = AsyncMock(return_value=None)

    sdk_runner = MagicMock()
    sdk_runner.run = AsyncMock(
        return_value=SimpleNamespace(
            final_text="an answer",
            llm_call_count=1,
            total_cost_usd=0.01,
            trace_id="trace_abc",
            usage=None,
            first_final_text_delta_elapsed_s=None,
        )
    )

    options_builder = MagicMock()
    options_builder.thinking_disabled = False
    options_builder.build.return_value = (MagicMock(), True)

    tool_runtime = MagicMock()
    tool_runtime.build_sdk_tools.return_value = []

    orchestrator = TurnOrchestrator(
        model="test-model",
        mcp_server_name="sefaria",
        prompt_service=prompt_service,
        create_mcp_server=MagicMock(),
        tool_runtime=tool_runtime,
        options_builder=options_builder,
        sdk_runner=sdk_runner,
        guardrail_gate=guardrail_gate,
        router=router,
        trace_logger=MagicMock(),
        logging_enabled=False,
    )
    return orchestrator, SimpleNamespace(
        prompt_service=prompt_service,
        router=router,
        trace_logger=orchestrator.trace_logger,
    )


async def run_turn_with(orchestrator, context):
    """Run a single turn with the link validator and tracing stubbed out."""
    validator = MagicMock()
    validator.validate_response = AsyncMock(
        return_value=SimpleNamespace(is_valid=True, issues=[])
    )
    with (
        patch("chat.V2.agent.turn_orchestrator.current_span", return_value=MagicMock()),
        patch(
            "chat.V2.agent.turn_orchestrator.ResponseLinkValidator",
            return_value=validator,
        ),
    ):
        return await orchestrator.run_turn(
            messages=[ConversationMessage(role="user", content="this reads wrong")],
            core_prompt_id="core-default",
            on_progress=None,
            context=context,
        )


def core_prompt_ids_requested(prompt_service):
    """Prompt ids the orchestrator asked for, in call order."""
    return [call.kwargs.get("prompt_id") for call in prompt_service.get_core_prompt.call_args_list]


class TestFlowSkipsRouter:
    @pytest.fixture(autouse=True)
    def _prompt_slugs(self, settings):
        settings.REPORT_ISSUE_PROMPT_SLUG = "report-issue"
        settings.RESPONSE_FORMAT_PROMPT_SLUG = "fmt"

    @pytest.mark.asyncio
    async def test_flow_turn_never_calls_the_router(self):
        orchestrator, mocks = build_orchestrator()

        await run_turn_with(orchestrator, MessageContext(flow=REPORT_ISSUE))

        mocks.router.run_router.assert_not_called()

    @pytest.mark.asyncio
    async def test_flow_turn_uses_the_flow_prompt(self):
        orchestrator, mocks = build_orchestrator()

        await run_turn_with(orchestrator, MessageContext(flow=REPORT_ISSUE))

        assert "report-issue" in core_prompt_ids_requested(mocks.prompt_service)
        assert "core-default" not in core_prompt_ids_requested(mocks.prompt_service)

    @pytest.mark.asyncio
    async def test_flow_turn_is_tagged_with_the_flow_name_for_tracing(self):
        orchestrator, mocks = build_orchestrator()

        await run_turn_with(orchestrator, MessageContext(flow=REPORT_ISSUE))

        assert mocks.trace_logger.log_prompt_metadata.call_args.kwargs["route"] == REPORT_ISSUE

    @pytest.mark.asyncio
    async def test_flow_prompt_wins_even_when_the_router_would_have_overridden(self):
        """The router would send 'this reads wrong' to the translation prompt.

        This is the regression that matters most: the router's deterministic
        classifier keys on the word 'translation', so a report about a
        translation is exactly the message it would hijack.
        """
        orchestrator, mocks = build_orchestrator(
            router_result=("translation-prompt", "translation", None)
        )

        await run_turn_with(orchestrator, MessageContext(flow=REPORT_ISSUE))

        assert "translation-prompt" not in core_prompt_ids_requested(mocks.prompt_service)
        mocks.router.run_router.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_flow_still_runs_the_router(self):
        orchestrator, mocks = build_orchestrator()

        await run_turn_with(orchestrator, MessageContext(flow=None))

        mocks.router.run_router.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_flow_still_honours_a_router_override(self):
        orchestrator, mocks = build_orchestrator(
            router_result=("translation-prompt", "translation", None)
        )

        await run_turn_with(orchestrator, MessageContext(flow=None))

        assert "translation-prompt" in core_prompt_ids_requested(mocks.prompt_service)

    @pytest.mark.asyncio
    async def test_unknown_flow_falls_back_to_the_router(self):
        """A stale client must not be able to switch routing off."""
        orchestrator, mocks = build_orchestrator()

        await run_turn_with(orchestrator, MessageContext(flow="made_up_flow"))

        mocks.router.run_router.assert_called_once()


@pytest.mark.django_db
class TestFlowReachesTheAgentFromTheWire:
    """The flow has to survive the HTTP boundary, not just the orchestrator."""

    @pytest.fixture
    def client(self):
        from rest_framework.test import APIClient

        return APIClient()

    @pytest.fixture
    def request_data(self):
        from django.utils import timezone

        from chat.tests.test_streaming_integration import create_test_token

        return {
            "userId": create_test_token("user_123", "test-secret-key-for-tokens"),
            "sessionId": "sess_report_1",
            "messageId": "msg_report_1",
            "timestamp": timezone.now().isoformat(),
            "text": 'Genesis 1:1 says:\nEnglish: "..."\n\nComment: "man" is wrong here',
        }

    def post_and_capture_context(self, client, data):
        """POST the stream endpoint and return the MessageContext the agent saw."""
        from chat.V2.agent import AgentResponse

        mock_agent = MagicMock()
        mock_agent.send_message = AsyncMock(
            return_value=AgentResponse(
                content="ok", tool_calls=[], latency_ms=1, trace_id="t"
            )
        )
        with (
            override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens"),
            patch("chat.V2.views.get_agent_service", return_value=mock_agent),
        ):
            response = client.post("/api/v2/chat/stream", data=data, format="json")
            # SSE body is lazy — consume it so the agent actually runs.
            b"".join(response.streaming_content)

        assert mock_agent.send_message.call_args is not None, "agent was never called"
        return mock_agent.send_message.call_args.kwargs["context"]

    def test_declared_flow_reaches_the_agent(self, client, request_data):
        request_data["context"] = {"flow": "report_issue"}

        context = self.post_and_capture_context(client, request_data)

        assert context.flow == REPORT_ISSUE

    def test_absent_flow_leaves_context_unflowed(self, client, request_data):
        context = self.post_and_capture_context(client, request_data)

        assert context.flow is None

    def test_unknown_flow_is_dropped_at_the_boundary(self, client, request_data):
        """Normalisation happens on the way in, so nothing downstream sees junk."""
        request_data["context"] = {"flow": "../../etc/passwd"}

        context = self.post_and_capture_context(client, request_data)

        assert context.flow is None

    def test_non_string_flow_does_not_break_the_request(self, client, request_data):
        request_data["context"] = {"flow": {"nested": "object"}}

        context = self.post_and_capture_context(client, request_data)

        assert context.flow is None

    def test_absurdly_long_flow_does_not_break_the_request(self, client, request_data):
        request_data["context"] = {"flow": "x" * 5000}

        context = self.post_and_capture_context(client, request_data)

        assert context.flow is None

    def test_flow_is_accepted_alongside_the_rest_of_the_context(self, client, request_data):
        """Adding `flow` must not disturb the fields already on the wire."""
        request_data["context"] = {
            "flow": "report_issue",
            "pageUrl": "https://www.sefaria.org/Genesis.1.1",
            "locale": "en",
            "isStaff": True,
        }

        context = self.post_and_capture_context(client, request_data)

        assert context.flow == REPORT_ISSUE
        assert context.page_url == "https://www.sefaria.org/Genesis.1.1"
        assert context.is_staff is True
