"""Tests for OpenAI-compatible chat completions endpoint."""

import pytest
from rest_framework.test import APIClient


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
