"""Logged-out (anonymous) visitors use the chat endpoints with an anonId instead of a userId."""

import hashlib
import json
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.core.cache import cache
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

LIMIT_FUNCTION = "chat.anon_access.get_anon_answer_limit"

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("agent", "no_limit_by_default")]


@pytest.fixture
def no_limit_by_default():
    """Tests that don't set a limit run without one (no Sefaria remote config)."""
    cache.clear()
    with patch(LIMIT_FUNCTION, return_value=None) as limit:
        yield limit


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


def ask(client, identity, message_id=MESSAGE_ID, session_id=SESSION_ID):
    return client.post(
        "/api/v2/chat/stream",
        data={
            **identity,
            "sessionId": session_id,
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


# ── The free-answer limit ─────────────────────────────────────────────────


def final_message(response):
    """The final `message` event of a streamed answer."""
    content = answered(response)
    events = []
    current = None
    for line in content.split("\n"):
        if line.startswith("event: "):
            current = line[len("event: ") :].strip()
        elif line.startswith("data: ") and current == "message":
            events.append(json.loads(line[len("data: ") :]))
    assert len(events) == 1
    return events[0]


def assert_login_required(response):
    assert response.status_code == 403
    assert json.loads(response.content) == {"error": "login_required"}


class Visitor:
    """A logged-out visitor asking questions; each gets its own message id."""

    def __init__(self, client, anon_id=ANON_ID, session_id=SESSION_ID):
        self.client = client
        self.anon_id = anon_id
        self.session_id = session_id
        self.asked = 0

    def ask(self, session_id=None):
        self.asked += 1
        return ask(
            self.client,
            {"anonId": self.anon_id},
            message_id=f"msg_{self.anon_id[:8]}_{self.asked}",
            session_id=session_id or self.session_id,
        )

    def last_message_id(self):
        return f"msg_{self.anon_id[:8]}_{self.asked}"

    def use_up(self, limit):
        for _ in range(limit):
            final_message(self.ask())


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_each_answer_reports_how_many_are_left(client, no_limit_by_default):
    limit = no_limit_by_default.return_value = 3
    visitor = Visitor(client)

    remaining = [final_message(visitor.ask())["anonResponsesRemaining"] for _ in range(limit)]

    assert remaining == list(range(limit - 1, -1, -1))


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_send_after_the_last_free_answer_needs_login(client, agent, no_limit_by_default):
    limit = no_limit_by_default.return_value = 2
    visitor = Visitor(client)
    visitor.use_up(limit)

    response = visitor.ask()

    assert_login_required(response)
    assert agent.send_message.await_count == limit
    assert not ChatMessage.objects.filter(message_id=visitor.last_message_id()).exists()


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_the_limit_counts_across_sessions(client, no_limit_by_default):
    limit = no_limit_by_default.return_value = 2
    visitor = Visitor(client)
    visitor.use_up(limit)

    assert_login_required(visitor.ask(session_id="sess_anon_other"))


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_the_limit_never_resets(client, no_limit_by_default):
    limit = no_limit_by_default.return_value = 2
    visitor = Visitor(client)
    visitor.use_up(limit)
    long_ago = timezone.now() - timedelta(days=365)
    ChatMessage.objects.filter(user_id=anonymous_user_id(ANON_ID)).update(server_timestamp=long_ago)

    assert_login_required(visitor.ask())


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_each_anonymous_id_has_its_own_limit(client, no_limit_by_default):
    limit = no_limit_by_default.return_value = 2
    Visitor(client).use_up(limit)

    other = Visitor(client, anon_id=OTHER_ANON_ID, session_id="sess_other")

    assert final_message(other.ask())["anonResponsesRemaining"] == limit - 1


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_a_refused_question_does_not_use_up_a_free_answer(client, agent, no_limit_by_default):
    limit = no_limit_by_default.return_value = 2
    agent.send_message.return_value = AgentResponse(
        content="I can only help with Jewish texts.",
        tool_calls=[],
        latency_ms=10,
        trace_id="trace_refused",
        refused=True,
    )
    visitor = Visitor(client)

    refused = final_message(visitor.ask())

    assert refused["anonResponsesRemaining"] == limit
    assert (
        ChatMessage.objects.get(
            user_id=anonymous_user_id(ANON_ID), role=ChatMessage.Role.ASSISTANT
        ).status
        == ChatMessage.Status.REFUSED
    )


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_a_failed_turn_does_not_use_up_a_free_answer(client, agent, no_limit_by_default):
    limit = no_limit_by_default.return_value = 2
    agent.send_message.side_effect = [
        Exception("Agent crashed"),
        AgentResponse(content="An answer", tool_calls=[], latency_ms=10, trace_id="t1"),
    ]
    visitor = Visitor(client)
    with patch("chat.V2.views.capture_exception"):
        answered(visitor.ask())

    assert final_message(visitor.ask())["anonResponsesRemaining"] == limit - 1


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_a_cancelled_turn_does_not_use_up_a_free_answer(client, no_limit_by_default):
    limit = no_limit_by_default.return_value = 2
    ChatSession.objects.create(session_id=SESSION_ID, user_id=anonymous_user_id(ANON_ID))
    ChatMessage.objects.create(
        message_id="msg_cancelled",
        session_id=SESSION_ID,
        user_id=anonymous_user_id(ANON_ID),
        turn_id="turn_cancelled",
        role=ChatMessage.Role.USER,
        content="A stopped question",
        processing_state=ChatMessage.ProcessingState.CANCELLED,
    )

    assert final_message(Visitor(client).ask())["anonResponsesRemaining"] == limit - 1


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_raising_the_limit_lets_the_visitor_ask_again(client, no_limit_by_default):
    limit = no_limit_by_default.return_value = 2
    visitor = Visitor(client)
    visitor.use_up(limit)
    assert_login_required(visitor.ask())

    no_limit_by_default.return_value = limit + 1

    assert final_message(visitor.ask())["anonResponsesRemaining"] == 0


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_a_limit_of_zero_gives_no_answers(client, agent, no_limit_by_default):
    no_limit_by_default.return_value = 0

    assert_login_required(Visitor(client).ask())
    agent.send_message.assert_not_awaited()


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_without_a_limit_a_visitor_is_never_refused(client):
    visitor = Visitor(client)

    for _ in range(5):
        assert "anonResponsesRemaining" not in final_message(visitor.ask())


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_logged_in_users_have_no_free_answer_limit(client, no_limit_by_default):
    limit = no_limit_by_default.return_value = 1
    token = create_test_token("user_logged_in", SECRET)

    for n in range(limit + 2):
        response = ask(client, {"userId": token}, message_id=f"msg_user_{n}")
        assert "anonResponsesRemaining" not in final_message(response)


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_recover_reports_how_many_answers_are_left(client, no_limit_by_default):
    limit = no_limit_by_default.return_value = 2
    visitor = Visitor(client)
    final_message(visitor.ask())

    response = client.post(
        "/api/v2/chat/recover",
        data={
            "anonId": ANON_ID,
            "sessionId": SESSION_ID,
            "messageId": visitor.last_message_id(),
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["message"]["anonResponsesRemaining"] == limit - 1


# ── Claiming a logged-out visitor's chats on login ────────────────────────

LOGGED_IN_USER = "user_claimer"


def claim(client, user_token, anon_id=ANON_ID):
    return client.post(
        "/api/v2/anon/claim",
        data={"userId": user_token, "anonId": anon_id},
        format="json",
    )


@pytest.fixture
def user_token():
    return create_test_token(LOGGED_IN_USER, SECRET)


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_claim_moves_the_logged_out_chat_to_the_user(client, user_token):
    Visitor(client).use_up(2)

    response = claim(client, user_token)

    assert response.status_code == 200
    assert response.data == {"claimedSessions": 1}
    assert ChatSession.objects.get(session_id=SESSION_ID).user_id == LOGGED_IN_USER
    assert set(
        ChatMessage.objects.filter(session_id=SESSION_ID).values_list("user_id", flat=True)
    ) == {LOGGED_IN_USER}
    assert not ChatMessage.objects.filter(user_id=anonymous_user_id(ANON_ID)).exists()


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_the_claimed_chat_is_in_the_users_history(client, user_token):
    Visitor(client).use_up(2)
    assert claim(client, user_token).status_code == 200

    conversations = client.get("/api/history/conversations", {"userId": user_token})
    messages = client.get("/api/history", {"userId": user_token, "sessionId": SESSION_ID})

    assert [c["sessionId"] for c in conversations.data["conversations"]] == [SESSION_ID]
    assert len(messages.data["messages"]) == 4  # two questions, two answers


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_the_claimed_chat_stays_in_history_after_a_new_chat(client, user_token):
    Visitor(client).use_up(1)
    assert claim(client, user_token).status_code == 200

    final_message(ask(client, {"userId": user_token}, message_id="msg_new", session_id="sess_new"))

    conversations = client.get("/api/history/conversations", {"userId": user_token})
    assert {c["sessionId"] for c in conversations.data["conversations"]} == {
        SESSION_ID,
        "sess_new",
    }


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_the_user_can_keep_asking_in_the_claimed_chat(client, agent, user_token):
    Visitor(client).use_up(1)
    assert claim(client, user_token).status_code == 200

    response = ask(client, {"userId": user_token}, message_id="msg_after_login")

    assert final_message(response)["sessionId"] == SESSION_ID
    assert ChatMessage.objects.get(message_id="msg_after_login").user_id == LOGGED_IN_USER


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_without_a_claim_the_user_cannot_continue_the_logged_out_chat(client, user_token):
    # The reason the widget claims on login: the session still belongs to the anon ID.
    Visitor(client).use_up(1)

    with pytest.raises(Exception, match="different user"):
        ask(client, {"userId": user_token}, message_id="msg_unclaimed")


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
@pytest.mark.parametrize(
    "bad_token",
    [
        pytest.param("not-a-token", id="garbage"),
        pytest.param(
            create_test_token(
                LOGGED_IN_USER, SECRET, expires_at=timezone.now() - timedelta(hours=1)
            ),
            id="expired",
        ),
        pytest.param(create_test_token(LOGGED_IN_USER, "another-secret"), id="wrong-secret"),
    ],
)
def test_claim_with_a_bad_token_moves_nothing(client, bad_token):
    Visitor(client).use_up(1)

    response = claim(client, bad_token)

    assert response.status_code == 401
    assert ChatSession.objects.get(session_id=SESSION_ID).user_id == anonymous_user_id(ANON_ID)


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_claiming_twice_is_harmless(client, user_token):
    Visitor(client).use_up(1)
    assert claim(client, user_token).status_code == 200

    second = claim(client, user_token)

    assert second.status_code == 200
    assert second.data == {"claimedSessions": 0}
    assert ChatSession.objects.get(session_id=SESSION_ID).user_id == LOGGED_IN_USER


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_claim_moves_only_this_visitors_chats(client, user_token):
    Visitor(client).use_up(1)
    Visitor(client, anon_id=OTHER_ANON_ID, session_id="sess_other").use_up(1)
    ChatSession.objects.create(session_id="sess_someone", user_id="someone_else")
    ChatMessage.objects.create(
        message_id="msg_someone",
        session_id="sess_someone",
        user_id="someone_else",
        role=ChatMessage.Role.USER,
        content="Not yours",
    )

    assert claim(client, user_token).status_code == 200

    assert ChatSession.objects.get(session_id="sess_other").user_id == anonymous_user_id(
        OTHER_ANON_ID
    )
    assert ChatSession.objects.get(session_id="sess_someone").user_id == "someone_else"
    assert ChatMessage.objects.get(message_id="msg_someone").user_id == "someone_else"


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="no-anon-id"),
        pytest.param({"anonId": "not-a-uuid"}, id="anon-id-not-a-uuid"),
    ],
)
def test_claim_needs_a_valid_anon_id(client, user_token, body):
    response = client.post("/api/v2/anon/claim", data={"userId": user_token, **body}, format="json")

    assert response.status_code == 400


@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_claim_needs_a_user_token(client):
    response = client.post("/api/v2/anon/claim", data={"anonId": ANON_ID}, format="json")

    assert response.status_code == 400
