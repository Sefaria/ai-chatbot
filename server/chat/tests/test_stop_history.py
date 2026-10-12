"""Cancellation state remains meaningful after recovery and history navigation."""

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from chat.models import ChatMessage, ChatSession
from chat.tests.test_streaming_integration import create_test_token
from chat.V2 import views


@pytest.mark.django_db
@override_settings(CHATBOT_USER_TOKEN_SECRET="test-stop-history")
@pytest.mark.parametrize("state,expected", [("cancelled", "cancelled"), ("cancelling", "running")])
def test_recovery_distinguishes_requested_and_acknowledged_stop(state, expected):
    from django.utils import timezone

    ChatSession.objects.create(session_id="s", user_id="u")
    ChatMessage.objects.create(
        message_id="m",
        session_id="s",
        user_id="u",
        role="user",
        content="Question",
        processing_state=state,
        processing_heartbeat_at=timezone.now(),
    )
    response = APIClient().post(
        "/api/v2/chat/recover",
        {"userId": create_test_token("u", "test-stop-history"), "sessionId": "s", "messageId": "m"},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["status"] == expected


@pytest.mark.django_db
def test_startup_and_heartbeat_do_not_erase_a_stop_request():
    message = ChatMessage.objects.create(
        message_id="m",
        session_id="s",
        user_id="u",
        role="user",
        content="Question",
        processing_state="cancelling",
    )
    views._mark_turn_running(message.pk)
    views._mark_turn_heartbeat(message.pk)
    message.refresh_from_db()
    assert message.processing_state == "cancelling"
    assert message.processing_heartbeat_at is not None
    views._mark_turn_cancelled(message.pk)
    message.refresh_from_db()
    assert message.processing_state == "cancelled"
    assert message.processing_finished_at is not None


@pytest.mark.django_db
@override_settings(CHATBOT_USER_TOKEN_SECRET="test-stop-history")
def test_stopped_first_turn_remains_in_history_with_its_topics():
    from chat.V2.services.chat_service import save_user_message

    session = ChatSession.objects.create(session_id="s", user_id="u")
    # Exercise the normal save-on-send path before cancellation.
    actor = type("ActorStub", (), {"to_db_fields": lambda self: {"user_id": "u"}})()
    message = save_user_message(
        session,
        actor,
        "m",
        "t",
        "Question",
        processing_state="cancelled",
        appetizer_data={"topics": [{"topicTitle": "Shabbat"}]},
    )
    token = create_test_token("u", "test-stop-history")
    client = APIClient()
    conversations = client.get("/api/history/conversations", {"userId": token}).data
    assert conversations["conversations"][0]["sessionId"] == "s"
    detail = client.get("/api/history/conversations/s", {"userId": token}).data
    assert detail["messages"][0]["processingState"] == "cancelled"
    assert detail["messages"][0]["appetizerData"] == message.appetizer_data
