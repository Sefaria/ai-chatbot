"""Logged-out visitors: anonId auth and the free-response quota."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from chat.auth.auth_service import anonymous_user_id
from chat.models import ChatSession
from chat.tests.test_streaming_integration import create_test_token
from chat.V2.agent import AgentResponse

ANON_ID = "8f14e45f-ceea-467a-9575-7a0a2f1c3e11"
SECRET = "test-secret-key-for-tokens"


def _final_message(response) -> dict:
    content = b"".join(response.streaming_content).decode("utf-8")
    for block in content.split("\n\n"):
        if block.startswith("event: message"):
            return json.loads(block.split("data: ", 1)[1])
    raise AssertionError(f"no final message event in: {content}")


def _payload(n: int, **ids) -> dict:
    return {
        **ids,
        "sessionId": "sess_anon",
        "messageId": f"msg_anon_{n}",
        "timestamp": timezone.now().isoformat(),
        "text": "What is Shabbat?",
    }


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def agent():
    mock = MagicMock()
    mock.send_message = AsyncMock(
        return_value=AgentResponse(content="Answer", tool_calls=[], latency_ms=5, trace_id="t")
    )
    with patch("chat.V2.views.get_agent_service", return_value=mock):
        yield mock


@pytest.fixture(autouse=True)
def two_free_responses(settings):
    settings.CHATBOT_ANON_FREE_RESPONSES = 2


@pytest.mark.django_db
class TestAnonymousQuota:
    def test_anonymous_request_is_answered_under_hashed_id(self, client, agent):
        response = client.post("/api/v2/chat/stream", _payload(1, anonId=ANON_ID), format="json")

        assert response.status_code == 200
        assert _final_message(response)["anonResponsesRemaining"] == 1
        session = ChatSession.objects.get(session_id="sess_anon")
        assert session.user_id == anonymous_user_id(ANON_ID)
        assert ANON_ID not in session.user_id

    def test_third_request_requires_login(self, client, agent):
        for n in (1, 2):
            _final_message(
                client.post("/api/v2/chat/stream", _payload(n, anonId=ANON_ID), format="json")
            )

        response = client.post("/api/v2/chat/stream", _payload(3, anonId=ANON_ID), format="json")

        assert response.status_code == 403
        assert response.data["error"] == "login_required"
        assert agent.send_message.await_count == 2

    def test_quota_spans_sessions(self, client, agent):
        for n in (1, 2):
            data = _payload(n, anonId=ANON_ID) | {"sessionId": f"sess_anon_{n}"}
            _final_message(client.post("/api/v2/chat/stream", data, format="json"))

        data = _payload(3, anonId=ANON_ID) | {"sessionId": "sess_anon_3"}
        response = client.post("/api/v2/chat/stream", data, format="json")

        assert response.status_code == 403

    def test_failed_responses_do_not_use_quota(self, client, agent):
        agent.send_message.side_effect = RuntimeError("boom")
        b"".join(
            client.post(
                "/api/v2/chat/stream", _payload(1, anonId=ANON_ID), format="json"
            ).streaming_content
        )
        agent.send_message.side_effect = None

        response = client.post("/api/v2/chat/stream", _payload(2, anonId=ANON_ID), format="json")

        assert _final_message(response)["anonResponsesRemaining"] == 1

    @override_settings(CHATBOT_ANON_FREE_RESPONSES=0)
    def test_zero_free_responses_disables_anonymous_chat(self, client, agent):
        response = client.post("/api/v2/chat/stream", _payload(1, anonId=ANON_ID), format="json")

        assert response.status_code == 403
        agent.send_message.assert_not_called()

    @override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET, CHATBOT_ANON_FREE_RESPONSES=0)
    def test_signed_in_users_are_not_limited(self, client, agent):
        token = create_test_token("user_123", SECRET)
        response = client.post(
            "/api/v2/chat/stream", _payload(1, userId=token, anonId=ANON_ID), format="json"
        )

        assert response.status_code == 200
        assert "anonResponsesRemaining" not in _final_message(response)

    def test_recovery_accepts_anonymous_actor(self, client, agent):
        _final_message(
            client.post("/api/v2/chat/stream", _payload(1, anonId=ANON_ID), format="json")
        )

        response = client.post(
            "/api/v2/chat/recover",
            {"anonId": ANON_ID, "sessionId": "sess_anon", "messageId": "msg_anon_1"},
            format="json",
        )

        assert response.status_code == 200
        assert response.data["status"] == "complete"
        assert response.data["message"]["anonResponsesRemaining"] == 1

    def test_recovery_is_scoped_to_the_anonymous_id(self, client, agent):
        _final_message(
            client.post("/api/v2/chat/stream", _payload(1, anonId=ANON_ID), format="json")
        )

        response = client.post(
            "/api/v2/chat/recover",
            {
                "anonId": "another-visitor-000000",
                "sessionId": "sess_anon",
                "messageId": "msg_anon_1",
            },
            format="json",
        )

        assert response.data["requestFound"] is False
