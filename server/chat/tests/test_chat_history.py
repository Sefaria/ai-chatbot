import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from chat.models import ChatMessage, ChatSession
from chat.tests.test_streaming_integration import create_test_token

SECRET = "test-secret-key-for-tokens"


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def token():
    return create_test_token("synthetic-user-1", SECRET)


def _create_conversation(
    *,
    session_id: str,
    title: str = "",
    user_content: str,
    assistant_content: str = "<p>answer</p>",
    appetizer_data: dict | None = None,
):
    session = ChatSession.objects.create(
        session_id=session_id,
        user_id="synthetic-user-1",
        title=title,
        turn_count=1,
        message_count=2,
    )
    user_message = ChatMessage.objects.create(
        message_id=f"msg_user_{session_id}",
        session_id=session_id,
        user_id="synthetic-user-1",
        turn_id=f"turn_{session_id}",
        role=ChatMessage.Role.USER,
        content=user_content,
        client_timestamp=timezone.now(),
        page_url="https://www.sefaria.org/Genesis.1.1?lang=bi",
        appetizer_data=appetizer_data,
    )
    assistant_message = ChatMessage.objects.create(
        message_id=f"msg_assistant_{session_id}",
        session_id=session_id,
        user_id="synthetic-user-1",
        turn_id=f"turn_{session_id}",
        role=ChatMessage.Role.ASSISTANT,
        content=assistant_content,
        status=ChatMessage.Status.SUCCESS,
    )
    user_message.response_message = assistant_message
    user_message.save(update_fields=["response_message"])
    return session


@pytest.mark.django_db
@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_conversation_list_and_load_include_appetizer_data(client, token):
    appetizer = {
        "version": 1,
        "topics": [
            {
                "topicSlug": "shabbat",
                "topicTitle": "Shabbat",
                "topicUrl": "https://www.sefaria.org/topics/shabbat",
            }
        ],
    }
    _create_conversation(
        session_id="sess_history_a",
        title="Learning Shabbat",
        user_content="teach me about shabbat",
        appetizer_data=appetizer,
    )

    list_response = client.get(
        "/api/history/conversations",
        {"userId": token, "limit": 20, "offset": 0},
    )

    assert list_response.status_code == 200
    assert list_response.data["conversations"][0]["sessionId"] == "sess_history_a"
    assert list_response.data["conversations"][0]["title"] == "Learning Shabbat"

    detail_response = client.get(
        "/api/history/conversations/sess_history_a",
        {"userId": token},
    )

    assert detail_response.status_code == 200
    assert detail_response.data["messages"][0]["appetizerData"] == appetizer
    assert detail_response.data["messages"][0]["pageUrl"].endswith("Genesis.1.1?lang=bi")


@pytest.mark.django_db
@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_prompt_only_conversation_appears_before_response_finishes(client, token):
    session = ChatSession.objects.create(
        session_id="sess_processing",
        user_id="synthetic-user-1",
        title="what is teshuva?",
        turn_count=0,
        message_count=1,
    )
    ChatMessage.objects.create(
        message_id="msg_user_processing",
        session_id=session.session_id,
        user_id="synthetic-user-1",
        turn_id="turn_processing",
        role=ChatMessage.Role.USER,
        content="what is teshuva?",
        client_timestamp=timezone.now(),
        processing_state=ChatMessage.ProcessingState.RUNNING,
    )

    list_response = client.get("/api/history/conversations", {"userId": token})

    assert list_response.status_code == 200
    assert list_response.data["conversations"][0]["sessionId"] == "sess_processing"
    assert list_response.data["conversations"][0]["title"] == "what is teshuva?"


@pytest.mark.django_db
@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_missing_conversation_title_is_backfilled_from_first_prompt(client, token):
    long_prompt = (
        "   What are the main rabbinic interpretations of Esther hiding "
        "her identity before Achashverosh?   "
    )
    session = _create_conversation(
        session_id="sess_missing_title",
        title="",
        user_content=long_prompt,
    )
    expected_title = " ".join(long_prompt.split())[:64]

    list_response = client.get("/api/history/conversations", {"userId": token})

    assert list_response.status_code == 200
    assert list_response.data["conversations"][0]["title"] == expected_title
    session.refresh_from_db()
    assert session.title == expected_title
    assert session.title_updated_at is not None

    session.title = ""
    session.title_updated_at = None
    session.save(update_fields=["title", "title_updated_at"])

    detail_response = client.get(
        "/api/history/conversations/sess_missing_title",
        {"userId": token},
    )

    assert detail_response.status_code == 200
    assert detail_response.data["conversation"]["title"] == expected_title
    session.refresh_from_db()
    assert session.title == expected_title


@pytest.mark.django_db
@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_conversation_search_matches_title_message_and_appetizer(client, token):
    _create_conversation(
        session_id="sess_title_match",
        title="Mourning sources",
        user_content="hello",
    )
    _create_conversation(
        session_id="sess_message_match",
        title="Other",
        user_content="tell me about avraham",
    )
    _create_conversation(
        session_id="sess_appetizer_match",
        title="Other two",
        user_content="question",
        appetizer_data={
            "version": 1,
            "topics": [
                {
                    "topicSlug": "education",
                    "topicTitle": "Education",
                    "topicUrl": "https://www.sefaria.org/topics/education",
                }
            ],
        },
    )

    title_response = client.get(
        "/api/history/conversations", {"userId": token, "search": "mourning"}
    )
    message_response = client.get(
        "/api/history/conversations", {"userId": token, "search": "avraham avinu"}
    )
    appetizer_response = client.get(
        "/api/history/conversations", {"userId": token, "search": "education"}
    )

    assert [c["sessionId"] for c in title_response.data["conversations"]] == ["sess_title_match"]
    assert [c["sessionId"] for c in message_response.data["conversations"]] == [
        "sess_message_match"
    ]
    assert [c["sessionId"] for c in appetizer_response.data["conversations"]] == [
        "sess_appetizer_match"
    ]


@pytest.mark.django_db
@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_rename_and_delete_conversation(client, token):
    session = _create_conversation(
        session_id="sess_editable",
        title="Old title",
        user_content="hello",
    )

    rename_response = client.patch(
        "/api/history/conversations/sess_editable",
        {"userId": token, "title": "x" * 100},
        format="json",
    )

    assert rename_response.status_code == 200
    session.refresh_from_db()
    assert session.title == "x" * 64
    assert session.title_updated_at is not None

    delete_response = client.delete(
        "/api/history/conversations/sess_editable",
        {"userId": token},
        format="json",
    )

    assert delete_response.status_code == 200
    session.refresh_from_db()
    assert session.is_deleted is True
    assert session.deleted_at is not None
    assert ChatMessage.objects.filter(session_id="sess_editable").count() == 2

    list_response = client.get("/api/history/conversations", {"userId": token})
    assert list_response.data["conversations"] == []


# --- Token required on every history endpoint -------------------------------------------------

HISTORY_URLS = [
    ("get", "/api/history", {"sessionId": "sess_history_a"}),
    ("get", "/api/history/conversations", {}),
    ("get", "/api/history/conversations/sess_history_a", {}),
    ("patch", "/api/history/conversations/sess_history_a", {"title": "x"}),
    ("delete", "/api/history/conversations/sess_history_a", {}),
]


def _call(client, method, url, params, user_id):
    params = {**params, "userId": user_id}
    if method == "get":
        return client.get(url, params)
    return getattr(client, method)(url, params, format="json")


@pytest.mark.django_db
@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
@pytest.mark.parametrize("method,url,params", HISTORY_URLS)
def test_history_rejects_unverified_identity(client, method, url, params):
    _create_conversation(
        session_id="sess_history_a", title="Mine", user_content="synthetic message"
    )

    response = _call(client, method, url, params, "synthetic-user-1")

    assert response.status_code == 403
    assert "synthetic message" not in str(response.data)
    assert ChatSession.objects.get(session_id="sess_history_a").is_deleted is False
    assert ChatSession.objects.get(session_id="sess_history_a").title == "Mine"


@pytest.mark.django_db
@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_history_rejects_unverified_hashed_identity(client):
    import hashlib

    hashed = hashlib.sha256(b"synthetic-user-2").hexdigest()
    ChatSession.objects.create(
        session_id="sess_hashed", user_id=hashed, turn_count=1, message_count=1
    )

    for url in ("/api/history/conversations", "/api/history/conversations/sess_hashed"):
        assert client.get(url, {"userId": hashed, "sessionId": "sess_hashed"}).status_code == 403


@pytest.mark.django_db
@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_history_rejects_token_from_other_issuer(client):
    forged = create_test_token("synthetic-user-1", "some-other-secret")

    assert client.get("/api/history/conversations", {"userId": forged}).status_code == 403


@pytest.mark.django_db
@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_history_rejects_expired_token(client):
    from datetime import timedelta

    expired = create_test_token(
        "synthetic-user-1", SECRET, expires_at=timezone.now() - timedelta(hours=1)
    )

    assert client.get("/api/history/conversations", {"userId": expired}).status_code == 403


@pytest.mark.django_db
@override_settings(CHATBOT_USER_TOKEN_SECRET="")
@pytest.mark.parametrize("method,url,params", HISTORY_URLS)
def test_history_unset_secret_returns_503(client, method, url, params):
    _create_conversation(
        session_id="sess_history_a", title="Mine", user_content="synthetic message"
    )

    response = _call(client, method, url, params, "synthetic-user-1")

    assert response.status_code == 503
    assert "synthetic message" not in str(response.data)


@pytest.mark.django_db
@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_history_endpoint_accepts_token_minted_like_sefaria_project(client):
    """Mint the token the way Sefaria-Project's build_chatbot_user_token does."""
    import base64
    import hashlib
    import json
    import os
    from datetime import timedelta

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    sefaria_user_id = 424242
    hashed = hashlib.sha256(str(sefaria_user_id).encode("utf-8")).hexdigest()
    payload = {
        "id": hashed,
        "user_id": sefaria_user_id,
        "expiration": (timezone.now() + timedelta(hours=72)).replace(microsecond=0).isoformat(),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    nonce = os.urandom(12)
    key = hashlib.sha256(SECRET.encode("utf-8")).digest()
    token = base64.urlsafe_b64encode(
        nonce + AESGCM(key).encrypt(nonce, payload_bytes, None)
    ).decode("ascii")

    session = ChatSession.objects.create(
        session_id="sess_cross_repo", user_id=hashed, turn_count=1, message_count=1
    )
    ChatMessage.objects.create(
        message_id="msg_cross_repo",
        session_id=session.session_id,
        user_id=hashed,
        role="user",
        content="hello from sefaria",
        status=ChatMessage.Status.SUCCESS,
        server_timestamp=timezone.now(),
    )

    response = client.get("/api/history", {"userId": token, "sessionId": "sess_cross_repo"})

    assert response.status_code == 200
    assert [m["content"] for m in response.data["messages"]] == ["hello from sefaria"]


# --- Admin prompt reload ----------------------------------------------------------------------


@pytest.mark.parametrize("header", [None, "", "wrong"])
def test_reload_prompts_rejects_missing_or_wrong_admin_token(client, monkeypatch, header):
    monkeypatch.setenv("ADMIN_API_TOKEN", "admin-secret")
    headers = {} if header is None else {"HTTP_X_ADMIN_TOKEN": header}

    assert client.post("/api/admin/reload-prompts", **headers).status_code == 403


@pytest.mark.parametrize("header", [None, "", "anything"])
def test_reload_prompts_unset_admin_token_is_closed(client, monkeypatch, header):
    monkeypatch.delenv("ADMIN_API_TOKEN", raising=False)
    headers = {} if header is None else {"HTTP_X_ADMIN_TOKEN": header}

    assert client.post("/api/admin/reload-prompts", **headers).status_code == 403


def test_reload_prompts_accepts_correct_admin_token(client, monkeypatch):
    from unittest.mock import MagicMock, patch

    monkeypatch.setenv("ADMIN_API_TOKEN", "admin-secret")
    service = MagicMock()

    with patch("chat.views.get_prompt_service", return_value=service):
        response = client.post("/api/admin/reload-prompts", HTTP_X_ADMIN_TOKEN="admin-secret")

    assert response.status_code == 200
    service.invalidate_cache.assert_called_once()


@pytest.mark.django_db
@pytest.mark.parametrize("debug,expected", [(False, 503), (True, 200)])
def test_history_default_token_secret_only_allowed_in_debug(client, debug, expected):
    default = "secret"
    token = create_test_token("synthetic-user-1", default)
    with override_settings(CHATBOT_USER_TOKEN_SECRET=default, DEBUG=debug):
        response = client.get("/api/history/conversations", {"userId": token})
    assert response.status_code == expected


@pytest.mark.django_db
@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
@pytest.mark.parametrize("limit", ["abc", "0", "-5", "1.5"])
def test_history_rejects_invalid_limit(client, token, limit):
    response = client.get("/api/history", {"userId": token, "sessionId": "sess_x", "limit": limit})
    assert response.status_code == 400


@pytest.mark.django_db
@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_history_invalid_limit_still_requires_valid_identity(client):
    response = client.get(
        "/api/history", {"userId": "not-a-token", "sessionId": "sess_x", "limit": "abc"}
    )
    assert response.status_code == 403


@pytest.mark.django_db
@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
@pytest.mark.parametrize("method", ["patch", "delete"])
@pytest.mark.parametrize("bad", [12345, ["a"], {"a": 1}, True, 0, "x" * 5000])
def test_history_rejects_non_string_or_oversized_user_id(client, method, bad):
    _create_conversation(
        session_id="sess_history_a", title="Mine", user_content="synthetic message"
    )
    response = getattr(client, method)(
        "/api/history/conversations/sess_history_a",
        {"userId": bad, "title": "New"},
        format="json",
    )
    assert response.status_code == 403
    assert response.data["error"] == "invalid_userId"
    assert ChatSession.objects.get(session_id="sess_history_a").title == "Mine"
