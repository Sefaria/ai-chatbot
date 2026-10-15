"""Logged-out (anonymous) visitors use the chat endpoints with an anonId instead of a userId."""

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from chat.auth import anonymous_user_id
from chat.models import ChatMessage, ChatSession
from chat.tests.test_streaming_integration import (  # noqa: F401  (autouse fixture)
    create_test_token,
    mock_auxiliary_services,
)
from chat.V2.agent import AgentResponse

SECRET = "test-secret-key-for-tokens"
ANON_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
OTHER_ANON_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
SESSION_ID = "sess_anon"
MESSAGE_ID = "msg_anon"

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("agent")]


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def agent():
    """An agent that always answers, so no model is called."""
    mock = MagicMock()
    mock.send_message = AsyncMock(
        return_value=AgentResponse(
            content="An answer", tool_calls=[], latency_ms=10, trace_id="trace_anon"
        )
    )
    with patch("chat.V2.views.get_agent_service", return_value=mock):
        yield mock


def ask(client, identity, message_id=MESSAGE_ID):
    return client.post(
        "/api/v2/chat/stream",
        data={
            **identity,
            "sessionId": SESSION_ID,
            "messageId": message_id,
            "timestamp": timezone.now().isoformat(),
            "text": "A question",
        },
        format="json",
    )


def answered(response):
    assert response.status_code == 200, getattr(response, "data", response)
    return b"".join(response.streaming_content).decode("utf-8")


def test_anonymous_user_id_is_a_prefixed_hash():
    expected = "anon:" + hashlib.sha256(ANON_ID.encode("utf-8")).hexdigest()

    assert anonymous_user_id(ANON_ID) == expected


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_logged_out_question_is_answered(client):
    content = answered(ask(client, {"anonId": ANON_ID}))

    assert "event: message" in content
    assert "An answer" in content


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_logged_out_chat_is_stored_under_the_hashed_id_only(client):
    answered(ask(client, {"anonId": ANON_ID}))

    assert ChatSession.objects.get(session_id=SESSION_ID).user_id == anonymous_user_id(ANON_ID)
    assert set(
        ChatMessage.objects.filter(session_id=SESSION_ID).values_list("user_id", flat=True)
    ) == {anonymous_user_id(ANON_ID)}
    assert not ChatSession.objects.filter(user_id__contains=ANON_ID).exists()
    assert not ChatMessage.objects.filter(user_id__contains=ANON_ID).exists()


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_agent_gets_no_sefaria_user_for_a_logged_out_visitor(client, agent):
    answered(ask(client, {"anonId": ANON_ID}))

    context = agent.send_message.call_args.kwargs["context"]
    assert context.user_id == anonymous_user_id(ANON_ID)
    assert context.sefaria_user_id is None
    assert context.encrypted_user_token is None


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_recover_accepts_an_anon_id(client):
    answered(ask(client, {"anonId": ANON_ID}))

    response = client.post(
        "/api/v2/chat/recover",
        data={"anonId": ANON_ID, "sessionId": SESSION_ID, "messageId": MESSAGE_ID},
        format="json",
    )

    assert response.status_code == 200
    assert response.data["status"] == "complete"


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_cancel_finds_only_the_visitors_own_turn(client):
    answered(ask(client, {"anonId": ANON_ID}))

    own = client.post(
        "/api/v2/chat/cancel",
        data={"anonId": ANON_ID, "sessionId": SESSION_ID, "messageId": MESSAGE_ID},
        format="json",
    )
    other = client.post(
        "/api/v2/chat/cancel",
        data={"anonId": OTHER_ANON_ID, "sessionId": SESSION_ID, "messageId": MESSAGE_ID},
        format="json",
    )

    assert own.status_code == 200
    assert own.data["status"] == "complete"
    assert other.status_code == 404


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
@patch("chat.V2.views.capture_message")
def test_client_event_accepts_an_anon_id(mock_capture_message, client):
    response = client.post(
        "/api/v2/chat/client-event",
        data={
            "anonId": ANON_ID,
            "sessionId": SESSION_ID,
            "messageId": MESSAGE_ID,
            "timestamp": timezone.now().isoformat(),
            "event": "stream_missing_final_message",
        },
        format="json",
    )

    assert response.status_code == 200
    assert mock_capture_message.call_args.kwargs["user_id"] == anonymous_user_id(ANON_ID)


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
@pytest.mark.parametrize(
    "identity",
    [
        pytest.param({}, id="neither"),
        pytest.param({"anonId": "not-a-uuid"}, id="anon-id-not-a-uuid"),
        pytest.param(
            {"anonId": ANON_ID, "userId": create_test_token("user_both", SECRET)}, id="both"
        ),
    ],
)
def test_request_without_exactly_one_valid_identity_is_rejected(client, agent, identity):
    response = ask(client, identity)

    assert response.status_code == 400
    agent.send_message.assert_not_called()
    assert not ChatMessage.objects.filter(session_id=SESSION_ID).exists()


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_logged_in_question_is_still_stored_under_the_user(client):
    answered(ask(client, {"userId": create_test_token("user_logged_in", SECRET)}))

    assert ChatSession.objects.get(session_id=SESSION_ID).user_id == "user_logged_in"
