"""Tests for OpenAI-compatible chat completions endpoint."""

import pytest
from rest_framework.test import APIClient

from chat.serializers import OpenAIChatRequestSerializer, OpenAIMessageSerializer


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def valid_openai_request():
    return {"model": "sefaria-agent", "messages": [{"role": "user", "content": "What is Shabbat?"}]}


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
