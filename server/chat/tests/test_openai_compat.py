"""Tests for OpenAI-compatible chat completions endpoint."""

import inspect
from unittest.mock import AsyncMock, patch

import pytest
from rest_framework.test import APIClient

from chat.agent import AgentResponse
from chat.agent.claude_service import ClaudeAgentService
from chat.router import Flow, ReasonCode, SessionAction
from chat.router.router_service import PromptBundle, RouteResult, SafetyResult
from chat.serializers import OpenAIChatRequestSerializer, OpenAIMessageSerializer


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def valid_openai_request():
    return {"model": "sefaria-agent", "messages": [{"role": "user", "content": "What is Shabbat?"}]}


@pytest.fixture
def mock_agent_response():
    return AgentResponse(
        content="Shabbat is the Jewish day of rest...",
        tool_calls=[],
        llm_calls=1,
        input_tokens=150,
        output_tokens=280,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        latency_ms=500,
        was_refused=False,
    )


@pytest.fixture
def mock_send_message(mock_agent_response):
    """Create an AsyncMock for send_message with the expected return value."""
    return AsyncMock(return_value=mock_agent_response)


@pytest.fixture
def mock_route_result():
    return RouteResult(
        flow=Flow.GENERAL,
        confidence=0.92,
        reason_codes=[ReasonCode.ROUTE_GENERAL_LEARNING],
        decision_id="route-test123",
        prompt_bundle=PromptBundle(
            core_prompt_id="core-id",
            core_prompt_version="v1",
            flow_prompt_id="flow-id",
            flow_prompt_version="v1",
        ),
        tools=["text_search"],
        session_action=SessionAction.CONTINUE,
        safety=SafetyResult(allowed=True),
        router_latency_ms=50,
    )


class TestOpenAICompatValidation:
    """Test request validation for OpenAI-compatible endpoint."""

    def test_rejects_missing_messages(self, api_client):
        response = api_client.post(
            "/api/v1/chat/completions", data={"model": "sefaria-agent"}, format="json"
        )
        assert response.status_code == 400
        assert "error" in response.json()
        assert response.json()["error"]["type"] == "invalid_request_error"

    def test_rejects_empty_messages(self, api_client):
        response = api_client.post(
            "/api/v1/chat/completions",
            data={"model": "sefaria-agent", "messages": []},
            format="json",
        )
        assert response.status_code == 400
        assert "error" in response.json()

    def test_rejects_invalid_message_format(self, api_client):
        response = api_client.post(
            "/api/v1/chat/completions",
            data={"model": "sefaria-agent", "messages": ["not a dict"]},
            format="json",
        )
        assert response.status_code == 400

    def test_rejects_message_missing_content(self, api_client):
        response = api_client.post(
            "/api/v1/chat/completions",
            data={"model": "sefaria-agent", "messages": [{"role": "user"}]},
            format="json",
        )
        assert response.status_code == 400

    def test_rejects_message_missing_role(self, api_client):
        response = api_client.post(
            "/api/v1/chat/completions",
            data={"model": "sefaria-agent", "messages": [{"content": "hello"}]},
            format="json",
        )
        assert response.status_code == 400


class TestOpenAISerializers:
    """Test OpenAI format serializers."""

    def test_message_serializer_valid(self):
        serializer = OpenAIMessageSerializer(data={"role": "user", "content": "Hello"})
        assert serializer.is_valid()

    def test_message_serializer_missing_role(self):
        serializer = OpenAIMessageSerializer(data={"content": "Hello"})
        assert not serializer.is_valid()
        assert "role" in serializer.errors

    def test_message_serializer_missing_content(self):
        serializer = OpenAIMessageSerializer(data={"role": "user"})
        assert not serializer.is_valid()
        assert "content" in serializer.errors

    def test_message_serializer_invalid_role(self):
        serializer = OpenAIMessageSerializer(data={"role": "invalid", "content": "Hello"})
        assert not serializer.is_valid()

    def test_request_serializer_valid(self):
        serializer = OpenAIChatRequestSerializer(
            data={"model": "sefaria-agent", "messages": [{"role": "user", "content": "Hello"}]}
        )
        assert serializer.is_valid()

    def test_request_serializer_defaults_model(self):
        serializer = OpenAIChatRequestSerializer(
            data={"messages": [{"role": "user", "content": "Hello"}]}
        )
        assert serializer.is_valid()
        assert serializer.validated_data["model"] == "sefaria-agent"

    def test_request_serializer_rejects_empty_messages(self):
        serializer = OpenAIChatRequestSerializer(data={"model": "sefaria-agent", "messages": []})
        assert not serializer.is_valid()
        assert "messages" in serializer.errors


@pytest.mark.django_db
class TestOpenAICompatResponse:
    """Test response transformation for OpenAI-compatible endpoint."""

    @patch("chat.views.get_agent_service")
    @patch("chat.views.get_router")
    def test_returns_openai_format_structure(
        self,
        mock_router,
        mock_agent,
        api_client,
        valid_openai_request,
        mock_send_message,
        mock_route_result,
    ):
        mock_router.return_value.route.return_value = mock_route_result
        mock_agent.return_value.send_message = mock_send_message

        response = api_client.post(
            "/api/v1/chat/completions", data=valid_openai_request, format="json"
        )

        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert data["id"].startswith("chatcmpl-")
        assert data["object"] == "chat.completion"
        assert "created" in data
        assert data["model"] == "sefaria-agent"
        assert "choices" in data
        assert len(data["choices"]) == 1

    @patch("chat.views.get_agent_service")
    @patch("chat.views.get_router")
    def test_includes_usage_tokens(
        self,
        mock_router,
        mock_agent,
        api_client,
        valid_openai_request,
        mock_send_message,
        mock_route_result,
    ):
        mock_router.return_value.route.return_value = mock_route_result
        mock_agent.return_value.send_message = mock_send_message

        response = api_client.post(
            "/api/v1/chat/completions", data=valid_openai_request, format="json"
        )

        data = response.json()
        assert "usage" in data
        assert data["usage"]["prompt_tokens"] == 150
        assert data["usage"]["completion_tokens"] == 280
        assert data["usage"]["total_tokens"] == 430

    @patch("chat.views.get_agent_service")
    @patch("chat.views.get_router")
    def test_includes_routing_metadata(
        self,
        mock_router,
        mock_agent,
        api_client,
        valid_openai_request,
        mock_send_message,
        mock_route_result,
    ):
        mock_router.return_value.route.return_value = mock_route_result
        mock_agent.return_value.send_message = mock_send_message

        response = api_client.post(
            "/api/v1/chat/completions", data=valid_openai_request, format="json"
        )

        data = response.json()
        assert "routing" in data
        assert data["routing"]["flow"] == "GENERAL"
        assert data["routing"]["confidence"] == 0.92
        assert data["routing"]["decision_id"] == "route-test123"
        assert data["routing"]["was_refused"] is False

    @patch("chat.views.get_agent_service")
    @patch("chat.views.get_router")
    def test_maps_content_to_choices(
        self,
        mock_router,
        mock_agent,
        api_client,
        valid_openai_request,
        mock_send_message,
        mock_route_result,
    ):
        mock_router.return_value.route.return_value = mock_route_result
        mock_agent.return_value.send_message = mock_send_message

        response = api_client.post(
            "/api/v1/chat/completions", data=valid_openai_request, format="json"
        )

        data = response.json()
        choice = data["choices"][0]
        assert choice["index"] == 0
        assert choice["message"]["role"] == "assistant"
        assert choice["message"]["content"] == "Shabbat is the Jewish day of rest..."
        assert choice["finish_reason"] == "stop"


@pytest.mark.django_db
class TestOpenAICompatMultiTurn:
    """Test multi-turn conversation handling."""

    @patch("chat.views.get_agent_service")
    @patch("chat.views.get_router")
    def test_extracts_last_user_message(
        self, mock_router, mock_agent, api_client, mock_send_message, mock_route_result
    ):
        mock_router.return_value.route.return_value = mock_route_result
        mock_agent.return_value.send_message = mock_send_message

        response = api_client.post(
            "/api/v1/chat/completions",
            data={
                "model": "sefaria-agent",
                "messages": [
                    {"role": "user", "content": "First question"},
                    {"role": "assistant", "content": "First answer"},
                    {"role": "user", "content": "Follow-up question"},
                ],
            },
            format="json",
        )

        assert response.status_code == 200
        # Verify the router was called with the last user message
        call_args = mock_router.return_value.route.call_args
        assert call_args.kwargs["user_message"] == "Follow-up question"

    @patch("chat.views.get_agent_service")
    @patch("chat.views.get_router")
    def test_handles_system_message(
        self, mock_router, mock_agent, api_client, mock_send_message, mock_route_result
    ):
        mock_router.return_value.route.return_value = mock_route_result
        mock_agent.return_value.send_message = mock_send_message

        response = api_client.post(
            "/api/v1/chat/completions",
            data={
                "model": "sefaria-agent",
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant"},
                    {"role": "user", "content": "What is Shabbat?"},
                ],
            },
            format="json",
        )

        assert response.status_code == 200


@pytest.mark.django_db
class TestOpenAICompatErrors:
    """Test error handling for OpenAI-compatible endpoint."""

    @patch("chat.views.get_agent_service")
    @patch("chat.views.get_router")
    def test_agent_error_returns_openai_error_format(
        self, mock_router, mock_agent, api_client, valid_openai_request, mock_route_result
    ):
        mock_router.return_value.route.return_value = mock_route_result
        mock_agent.return_value.send_message = AsyncMock(side_effect=Exception("Agent failed"))

        response = api_client.post(
            "/api/v1/chat/completions",
            data=valid_openai_request,
            format="json",
        )

        assert response.status_code == 500
        data = response.json()
        assert "error" in data
        assert data["error"]["type"] == "internal_error"
        assert "Agent failed" in data["error"]["message"]


@pytest.mark.django_db
class TestOpenAICompatTraceability:
    """Test logging and traceability."""

    @patch("chat.views.get_agent_service")
    @patch("chat.views.get_router")
    def test_generates_bt_prefixed_session_id(
        self,
        mock_router,
        mock_agent,
        api_client,
        valid_openai_request,
        mock_send_message,
        mock_route_result,
    ):
        mock_router.return_value.route.return_value = mock_route_result
        mock_agent.return_value.send_message = mock_send_message

        api_client.post(
            "/api/v1/chat/completions",
            data=valid_openai_request,
            format="json",
        )

        # Verify session_id passed to router starts with bt-
        call_args = mock_router.return_value.route.call_args
        assert call_args.kwargs["session_id"].startswith("bt-")

    @patch("chat.views.get_agent_service")
    @patch("chat.views.get_router")
    def test_sets_braintrust_source_in_context(
        self,
        mock_router,
        mock_agent,
        api_client,
        valid_openai_request,
        mock_send_message,
        mock_route_result,
    ):
        mock_router.return_value.route.return_value = mock_route_result
        mock_agent.return_value.send_message = mock_send_message

        api_client.post(
            "/api/v1/chat/completions",
            data=valid_openai_request,
            format="json",
        )

        # Verify agent was called with braintrust source
        call_args = mock_agent.return_value.send_message.call_args
        assert call_args.kwargs.get("source") == "braintrust"


@pytest.mark.django_db
class TestOpenAICompatRefusals:
    """Test refusal handling for OpenAI-compatible endpoint."""

    @pytest.fixture
    def mock_refuse_route_result(self):
        return RouteResult(
            flow=Flow.REFUSE,
            confidence=1.0,
            reason_codes=[ReasonCode.GUARDRAIL_DISALLOWED_CONTENT],
            decision_id="route-refuse123",
            prompt_bundle=PromptBundle(
                core_prompt_id="core-id",
                core_prompt_version="v1",
                flow_prompt_id="refuse-id",
                flow_prompt_version="v1",
            ),
            tools=[],
            session_action=SessionAction.END,
            safety=SafetyResult(allowed=False, refusal_message="I can't help with that request."),
            router_latency_ms=10,
        )

    @patch("chat.views.get_agent_service")
    @patch("chat.views.get_router")
    def test_refusal_returns_early_without_calling_agent(
        self, mock_router, mock_agent, api_client, valid_openai_request, mock_refuse_route_result
    ):
        """Verify that REFUSE flow returns early without calling the agent."""
        mock_router.return_value.route.return_value = mock_refuse_route_result

        response = api_client.post(
            "/api/v1/chat/completions",
            data=valid_openai_request,
            format="json",
        )

        # Agent should NOT be called for refusals
        mock_agent.return_value.send_message.assert_not_called()

        # Should still return 200 with refusal content
        assert response.status_code == 200
        data = response.json()
        assert data["choices"][0]["message"]["content"] == "I can't help with that request."
        assert data["choices"][0]["finish_reason"] == "content_filter"
        assert data["routing"]["flow"] == "REFUSE"
        assert data["routing"]["was_refused"] is True


class TestPageContextContract:
    """Contract tests to ensure views pass valid parameters to agent service."""

    def test_page_context_keys_match_send_message_signature(self):
        """
        Ensure page_context keys from views.py match send_message parameters.

        This catches bugs where views pass kwargs that send_message doesn't accept.
        """
        from chat.views import extract_page_context

        # Get the page_context keys that views.py passes to send_message
        page_context = extract_page_context(
            {"pageUrl": "https://example.com", "clientVersion": "1.0"}
        )

        # Get the send_message parameter names
        sig = inspect.signature(ClaudeAgentService.send_message)
        valid_params = set(sig.parameters.keys())

        # Verify all page_context keys are valid parameters
        for key in page_context.keys():
            assert key in valid_params, (
                f"page_context has '{key}' but ClaudeAgentService.send_message doesn't accept it. "
                f"Valid params: {sorted(valid_params)}"
            )
