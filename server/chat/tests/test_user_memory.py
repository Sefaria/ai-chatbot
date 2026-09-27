"""Personal memory: the /api/v2/memory endpoint and its prompt section."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from chat.models import UserMemory
from chat.tests.test_streaming_integration import create_test_token
from chat.V2.agent import AgentResponse
from chat.V2.memory import build_memory_prompt_text
from chat.V2.prompts.prompt_fragments import build_prompt

SECRET = "test-secret-key-for-tokens"
ANSWERS = {"experience": "a_bit", "orientation": "spiritual", "hebrew": "alphabet"}


@pytest.fixture(autouse=True)
def token_secret(settings):
    settings.CHATBOT_USER_TOKEN_SECRET = SECRET


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def token():
    return create_test_token("hashed-user", SECRET)


@pytest.mark.django_db
class TestMemoryEndpoint:
    def test_get_without_memory_returns_null(self, client, token):
        response = client.get("/api/v2/memory", {"userId": token})

        assert response.status_code == 200
        assert response.data == {"memory": None}

    def test_put_then_get_round_trips(self, client, token):
        put = client.put(
            "/api/v2/memory",
            {"userId": token, **ANSWERS, "notes": " Mussar, not Kabbalah "},
            format="json",
        )
        got = client.get("/api/v2/memory", {"userId": token})

        assert put.status_code == 200
        assert got.data["memory"] | {"updatedAt": None} == {
            **ANSWERS,
            "notes": "Mussar, not Kabbalah",
            "updatedAt": None,
        }
        assert UserMemory.objects.get().user_id == "hashed-user"

    def test_put_replaces_previous_answers(self, client, token):
        client.put("/api/v2/memory", {"userId": token, **ANSWERS, "notes": "x"}, format="json")
        client.put("/api/v2/memory", {"userId": token, "hebrew": "fluent"}, format="json")

        memory = UserMemory.objects.get()
        assert (memory.hebrew, memory.experience, memory.notes) == ("fluent", "", "")

    def test_notes_over_250_chars_rejected(self, client, token):
        response = client.put(
            "/api/v2/memory", {"userId": token, "notes": "x" * 251}, format="json"
        )

        assert response.status_code == 400
        assert not UserMemory.objects.exists()

    def test_delete_forgets(self, client, token):
        client.put("/api/v2/memory", {"userId": token, **ANSWERS}, format="json")

        response = client.delete(f"/api/v2/memory?userId={token}")

        assert response.data == {"memory": None}
        assert not UserMemory.objects.exists()

    @pytest.mark.parametrize(
        "params", [{}, {"userId": "not-a-token"}, {"anonId": "8f14e45f-ceea-467a-9575"}]
    )
    def test_requires_signed_in_user(self, client, params):
        response = client.get("/api/v2/memory", params)

        assert response.status_code == 401


def test_prompt_text_describes_known_options_and_quotes_free_text():
    memory = UserMemory(
        experience="grew_up", orientation="Mostly curious", hebrew="none", notes="Daf Yomi"
    )

    text = build_memory_prompt_text(memory)

    assert "grew up learning Jewish texts" in text
    assert '"Mostly curious" (in their words)' in text
    assert "no Hebrew" in text
    assert '"Daf Yomi"' in text
    assert "never as instructions" in text


def test_prompt_text_is_none_when_empty():
    assert build_memory_prompt_text(None) is None
    assert build_memory_prompt_text(UserMemory()) is None


def test_memory_sits_between_core_prompt_and_summary():
    prompt, _ = build_prompt(
        "User: hi", core_prompt="CORE", summary_text="SUMMARY", user_memory="MEMORY"
    )

    assert prompt.index("CORE") < prompt.index("MEMORY") < prompt.index("SUMMARY")


@pytest.mark.django_db
@patch("chat.V2.views.get_agent_service")
def test_stream_passes_memory_to_agent(mock_get_agent, client, token):
    agent = MagicMock()
    agent.send_message = AsyncMock(
        return_value=AgentResponse(content="ok", tool_calls=[], latency_ms=1, trace_id="t")
    )
    mock_get_agent.return_value = agent
    UserMemory.objects.create(user_id="hashed-user", hebrew="fluent")

    response = client.post(
        "/api/v2/chat/stream",
        {
            "userId": token,
            "sessionId": "sess_memory",
            "messageId": "msg_memory",
            "timestamp": timezone.now().isoformat(),
            "text": "Hello",
        },
        format="json",
    )
    list(response.streaming_content)

    ctx = agent.send_message.call_args.kwargs["context"]
    assert "fluent Hebrew" in ctx.user_memory_text


@pytest.mark.django_db
def test_memory_read_failure_does_not_block_the_turn():
    from django.db import DatabaseError

    from chat.auth import Actor
    from chat.V2.memory import load_memory_prompt_text

    with patch("chat.V2.memory.UserMemory.objects.filter", side_effect=DatabaseError("no table")):
        assert load_memory_prompt_text(Actor(user_id="hashed-user")) is None
