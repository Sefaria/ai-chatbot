"""Tests for ClaudeAgentService - refusal handling and response creation."""

from unittest.mock import MagicMock, patch

import pytest

from chat.agent.claude_service import AgentResponse, ClaudeAgentService, ConversationMessage
from chat.router.reason_codes import ReasonCode
from chat.router.router_service import (
    Flow,
    PromptBundle,
    RouteResult,
    SafetyResult,
    SessionAction,
)


@pytest.fixture
def mock_prompt_service():
    """Create a mock prompt service."""
    service = MagicMock()
    service.get_core_prompt.return_value = ("You are a helpful assistant.", "v1")
    service.get_flow_prompt.return_value = ("", "")
    return service


@pytest.fixture
def agent_service(mock_prompt_service):
    """Create an agent service with mocked dependencies."""
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        return ClaudeAgentService(
            api_key="test-key",
            prompt_service=mock_prompt_service,
        )


class TestCreateRefusalResponse:
    """Test _create_refusal_response method."""

    def test_refusal_response_with_reason_codes(self, agent_service):
        """Test that refusal response correctly extracts reason codes from RouteResult."""
        route_result = RouteResult(
            decision_id="test-decision-123",
            flow=Flow.REFUSE,
            confidence=1.0,
            reason_codes=[ReasonCode.GUARDRAIL_PROMPT_INJECTION],
            prompt_bundle=PromptBundle(),
            tools=[],
            session_action=SessionAction.END,
            safety=SafetyResult(
                allowed=False,
                refusal_message="This request has been blocked.",
            ),
        )

        messages = [ConversationMessage(role="user", content="malicious prompt")]

        with patch("chat.agent.claude_service.current_span") as mock_span:
            mock_span.return_value = MagicMock()

            response = agent_service._create_refusal_response(
                route_result=route_result,
                start_time=0.0,
                messages=messages,
                last_user_message="malicious prompt",
                session_id="session-123",
            )

        assert isinstance(response, AgentResponse)
        assert response.was_refused is True
        assert response.content == "This request has been blocked."
        assert response.flow == "REFUSE"

    def test_refusal_response_logs_correct_codes(self, agent_service):
        """Test that refusal codes are logged from route_result.reason_codes, not safety.reason_codes."""
        route_result = RouteResult(
            decision_id="test-decision-456",
            flow=Flow.REFUSE,
            confidence=1.0,
            reason_codes=[
                ReasonCode.GUARDRAIL_PROMPT_INJECTION,
                ReasonCode.GUARDRAIL_HARASSMENT,
            ],
            prompt_bundle=PromptBundle(),
            tools=[],
            session_action=SessionAction.END,
            safety=SafetyResult(
                allowed=False,
                refusal_message="Blocked for safety.",
            ),
        )

        messages = [ConversationMessage(role="user", content="bad request")]
        logged_output = {}

        def capture_log(**kwargs):
            if "output" in kwargs:
                logged_output.update(kwargs["output"])

        with patch("chat.agent.claude_service.current_span") as mock_current_span:
            mock_span = MagicMock()
            mock_span.log = capture_log
            mock_current_span.return_value = mock_span

            agent_service._create_refusal_response(
                route_result=route_result,
                start_time=0.0,
                messages=messages,
                last_user_message="bad request",
            )

        assert "refusal_codes" in logged_output
        assert logged_output["refusal_codes"] == [
            "GUARDRAIL_PROMPT_INJECTION",
            "GUARDRAIL_HARASSMENT",
        ]
