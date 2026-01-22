"""Tests for OpenAI-compatible chat completions endpoint."""

from unittest.mock import AsyncMock, patch

import pytest
from rest_framework.test import APIClient

from chat.agent import AgentResponse
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
        mock_agent_response,
        mock_route_result,
    ):
        mock_router.return_value.route.return_value = mock_route_result
        mock_agent.return_value.send_message = AsyncMock(return_value=mock_agent_response)

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
        mock_agent_response,
        mock_route_result,
    ):
        mock_router.return_value.route.return_value = mock_route_result
        mock_agent.return_value.send_message = AsyncMock(return_value=mock_agent_response)

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
        mock_agent_response,
        mock_route_result,
    ):
        mock_router.return_value.route.return_value = mock_route_result
        mock_agent.return_value.send_message = AsyncMock(return_value=mock_agent_response)

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
        mock_agent_response,
        mock_route_result,
    ):
        mock_router.return_value.route.return_value = mock_route_result
        mock_agent.return_value.send_message = AsyncMock(return_value=mock_agent_response)

        response = api_client.post(
            "/api/v1/chat/completions", data=valid_openai_request, format="json"
        )

        data = response.json()
        choice = data["choices"][0]
        assert choice["index"] == 0
        assert choice["message"]["role"] == "assistant"
        assert choice["message"]["content"] == "Shabbat is the Jewish day of rest..."
        assert choice["finish_reason"] == "stop"
