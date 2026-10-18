"""Stop acknowledgement must not depend on worker cleanup or log upload."""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from chat.models import ChatMessage
from chat.tests.test_streaming_integration import (  # noqa: F401
    create_test_token,
    mock_auxiliary_services,
)
from chat.V2.agent import AgentResponse


@pytest.mark.django_db(transaction=True)
@override_settings(CHATBOT_USER_TOKEN_SECRET="cleanup-test")
@pytest.mark.parametrize("blocked_phase", ["worker", "logging"])
def test_stop_and_resend_before_cleanup_finishes(blocked_phase):
    client = APIClient()
    token = create_test_token("u", "cleanup-test")
    release = threading.Event()
    blocked = threading.Event()
    finished = threading.Event()
    calls = []

    async def send(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1 and blocked_phase == "worker":
            blocked.set()
            release.wait(5)
        return AgentResponse(
            content="Late old answer" if len(calls) == 1 else "New answer",
            tool_calls=[],
            latency_ms=0,
        )

    flush_count = 0

    def flush():
        nonlocal flush_count
        flush_count += 1
        if blocked_phase == "logging" and flush_count == 1:
            blocked.set()
            release.wait(5)
        if release.is_set():
            finished.set()

    def request(message_id):
        return client.post(
            "/api/v2/chat/stream",
            {
                "userId": token,
                "sessionId": "s",
                "messageId": message_id,
                "timestamp": timezone.now().isoformat(),
                "text": message_id,
            },
            format="json",
        )

    with (
        patch("chat.V2.views.get_agent_service", return_value=MagicMock(send_message=send)),
        patch("chat.V2.views._flush_braintrust", side_effect=flush),
    ):
        stream = request("old").streaming_content
        next(stream)
        try:
            assert blocked.wait(2)
            start = time.monotonic()
            cancel = client.post(
                "/api/v2/chat/cancel",
                {"userId": token, "sessionId": "s", "messageId": "old"},
                format="json",
            )
            assert cancel.data["status"] == "cancelled"
            body = b"".join(stream)
            elapsed = time.monotonic() - start
            assert b"event: cancelled" in body
            assert elapsed < 2
            assert not release.is_set()
            # A second prompt completes while the first worker/log flush is still blocked.
            second = b"".join(request("new").streaming_content)
            assert b"event: message" in second
            print(
                f"{blocked_phase}: stop confirmed in {elapsed:.3f}s, next prompt completed before cleanup"
            )
        finally:
            release.set()
            assert finished.wait(2)
        old = ChatMessage.objects.get(message_id="old")
        new = ChatMessage.objects.get(message_id="new")
        assert old.processing_state == "cancelled"
        assert old.response_message_id is None
        assert new.processing_state == "completed"
        assert ChatMessage.objects.filter(role="assistant").count() == 1


@pytest.mark.django_db
@override_settings(CHATBOT_USER_TOKEN_SECRET="cleanup-test")
@pytest.mark.parametrize("outcome", ["success", "error"])
def test_stop_between_result_check_and_persistence(outcome):
    """Exercise both publication guards after the initial cancellation check."""
    client = APIClient()
    token = create_test_token("u", "cleanup-test")

    async def send(**kwargs):
        if outcome == "error":
            raise ValueError("late error")
        return AgentResponse(content="Answer", tool_calls=[], latency_ms=0)

    def intercept(message, *args, **kwargs):
        if message == "Agent completed" or str(message).startswith("Agent error:"):
            result = client.post(
                "/api/v2/chat/cancel",
                {"userId": token, "sessionId": "s", "messageId": "m"},
                format="json",
            )
            assert result.data["status"] == "cancelled"

    with (
        patch("chat.V2.views.get_agent_service", return_value=MagicMock(send_message=send)),
        patch("chat.V2.views.logger.info", side_effect=intercept),
        patch("chat.V2.views.logger.error", side_effect=intercept),
    ):
        response = client.post(
            "/api/v2/chat/stream",
            {
                "userId": token,
                "sessionId": "s",
                "messageId": "m",
                "timestamp": timezone.now().isoformat(),
                "text": "question",
            },
            format="json",
        )
        body = b"".join(response.streaming_content)
    assert b"event: cancelled" in body
    assert not ChatMessage.objects.filter(role="assistant").exists()
    assert ChatMessage.objects.get(message_id="m").processing_state == "cancelled"
