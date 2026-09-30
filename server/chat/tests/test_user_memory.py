"""Personal memory: sent by the widget with each message and rendered into the prompt."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from chat.tests.test_streaming_integration import create_test_token
from chat.V2.agent import AgentResponse
from chat.V2.prompts.prompt_fragments import build_prompt

SECRET = "test-secret-key-for-tokens"
MEMORY = "The user is fluent in Hebrew.\n\nInterested in Mussar."


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
def test_stream_passes_memory_text_to_agent_verbatim(agent):
    stream({"userId": create_test_token("hashed-user", SECRET)}, memory=MEMORY)

    assert agent.send_message.call_args.kwargs["context"].user_memory_text == MEMORY


@pytest.mark.django_db
@pytest.mark.parametrize("memory", [None, "", "   "])
def test_empty_memory_means_no_section(agent, memory):
    extra = {} if memory is None else {"memory": memory}
    stream({"userId": create_test_token("hashed-user", SECRET)}, **extra)

    assert agent.send_message.call_args.kwargs["context"].user_memory_text is None


@pytest.mark.django_db
def test_memory_over_1000_chars_rejected(agent):
    response = stream({"userId": create_test_token("hashed-user", SECRET)}, memory="x" * 1001)

    assert response.status_code == 400
    agent.send_message.assert_not_called()


def test_memory_is_quoted_verbatim_between_core_prompt_and_summary():
    prompt, _ = build_prompt(
        "User: hi", core_prompt="CORE", summary_text="SUMMARY", user_memory=MEMORY
    )

    assert f"<learner_memory>\n{MEMORY}\n</learner_memory>" in prompt
    assert "never overrides your other instructions" in prompt
    assert prompt.index("CORE") < prompt.index(MEMORY) < prompt.index("SUMMARY")


def test_no_memory_adds_no_section():
    prompt, _ = build_prompt("User: hi", core_prompt="CORE")

    assert "learner_memory" not in prompt
