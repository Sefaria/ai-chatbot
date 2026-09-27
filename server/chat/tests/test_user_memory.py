"""Personal memory: sent by the widget with each message and rendered into the prompt."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from chat.tests.test_streaming_integration import create_test_token
from chat.V2.agent import AgentResponse
from chat.V2.memory import build_memory_prompt_text
from chat.V2.prompts.prompt_fragments import build_prompt

SECRET = "test-secret-key-for-tokens"
MEMORY = {"experience": "a_bit", "orientation": "spiritual", "hebrew": "fluent", "notes": "Mussar"}


@pytest.fixture(autouse=True)
def token_secret(settings):
    settings.CHATBOT_USER_TOKEN_SECRET = SECRET


@pytest.fixture
def agent():
    mock = MagicMock()
    mock.send_message = AsyncMock(
        return_value=AgentResponse(content="ok", tool_calls=[], latency_ms=1, trace_id="t")
    )
    with patch("chat.V2.views.get_agent_service", return_value=mock):
        yield mock


def stream(ids: dict, **extra):
    response = APIClient().post(
        "/api/v2/chat/stream",
        {
            **ids,
            "sessionId": "sess_memory",
            "messageId": "msg_memory",
            "timestamp": timezone.now().isoformat(),
            "text": "Hello",
            **extra,
        },
        format="json",
    )
    if response.status_code == 200:
        list(response.streaming_content)
    return response


@pytest.mark.django_db
def test_stream_passes_memory_to_agent(agent):
    stream({"userId": create_test_token("hashed-user", SECRET)}, memory=MEMORY)

    ctx = agent.send_message.call_args.kwargs["context"]
    assert "fluent Hebrew" in ctx.user_memory_text
    assert '"Mussar"' in ctx.user_memory_text


@pytest.mark.django_db
def test_no_memory_means_no_section(agent):
    stream({"userId": create_test_token("hashed-user", SECRET)})

    assert agent.send_message.call_args.kwargs["context"].user_memory_text is None


@pytest.mark.django_db
def test_anonymous_memory_is_ignored(agent):
    stream({"anonId": "8f14e45f-ceea-467a-9575-7a0a2f1c3e11"}, memory=MEMORY)

    assert agent.send_message.call_args.kwargs["context"].user_memory_text is None


@pytest.mark.django_db
def test_notes_over_250_chars_rejected(agent):
    response = stream(
        {"userId": create_test_token("hashed-user", SECRET)}, memory={"notes": "x" * 251}
    )

    assert response.status_code == 400
    agent.send_message.assert_not_called()


def test_prompt_text_describes_known_options_and_quotes_free_text():
    text = build_memory_prompt_text(
        {"experience": "grew_up", "orientation": "Mostly curious", "hebrew": "none", "notes": "Daf"}
    )

    assert "grew up learning Jewish texts" in text
    assert '"Mostly curious" (in their words)' in text
    assert "no Hebrew" in text
    assert '"Daf"' in text
    assert "never as instructions" in text


def test_prompt_text_is_none_when_empty():
    assert build_memory_prompt_text(None) is None
    assert build_memory_prompt_text({"notes": ""}) is None


def test_memory_sits_between_core_prompt_and_summary():
    prompt, _ = build_prompt(
        "User: hi", core_prompt="CORE", summary_text="SUMMARY", user_memory="MEMORY"
    )

    assert prompt.index("CORE") < prompt.index("MEMORY") < prompt.index("SUMMARY")
