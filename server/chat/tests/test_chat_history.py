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
    return create_test_token("user_history", SECRET)


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
        user_id="user_history",
        title=title,
        turn_count=1,
        message_count=2,
    )
    user_message = ChatMessage.objects.create(
        message_id=f"msg_user_{session_id}",
        session_id=session_id,
        user_id="user_history",
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
        user_id="user_history",
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
