import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from chat.models import ChatMessage, ChatSession
from chat.tests.test_streaming_integration import create_test_token


@pytest.mark.django_db
class TestSavedConversations:
    @pytest.fixture
    def client(self):
        return APIClient()

    def _create_saved_conversation(self, session_id="sess_saved", title="First question"):
        session = ChatSession.objects.create(
            session_id=session_id,
            user_id="user_saved",
            title=title,
            turn_count=1,
            message_count=2,
        )
        ChatMessage.objects.create(
            message_id=f"msg_{session_id}_user",
            session_id=session_id,
            user_id="user_saved",
            role=ChatMessage.Role.USER,
            content="What is Shabbat?",
        )
        ChatMessage.objects.create(
            message_id=f"msg_{session_id}_assistant",
            session_id=session_id,
            user_id="user_saved",
            role=ChatMessage.Role.ASSISTANT,
            content="Shabbat is a day of rest.",
        )
        return session

    def test_lists_saved_conversations_for_user(self, client):
        self._create_saved_conversation()
        ChatSession.objects.create(
            session_id="sess_unsaved",
            user_id="user_saved",
            title="Draft",
            turn_count=0,
        )
        ChatSession.objects.create(
            session_id="sess_other",
            user_id="other_user",
            title="Other",
            turn_count=1,
        )

        response = client.get("/api/conversations", {"userId": "user_saved"})

        assert response.status_code == 200
        conversations = response.json()["conversations"]
        assert len(conversations) == 1
        assert conversations[0]["sessionId"] == "sess_saved"
        assert conversations[0]["title"] == "First question"

    @override_settings(CHATBOT_USER_TOKEN_SECRET="test-secret-key-for-tokens")
    def test_lists_saved_conversations_with_encrypted_user_token(self, client):
        self._create_saved_conversation()
        token = create_test_token("user_saved", "test-secret-key-for-tokens")

        response = client.get("/api/conversations", {"userId": token})

        assert response.status_code == 200
        conversations = response.json()["conversations"]
        assert len(conversations) == 1
        assert conversations[0]["sessionId"] == "sess_saved"

    def test_searches_titles_and_messages(self, client):
        self._create_saved_conversation(title="Different title")

        response = client.get("/api/conversations", {"userId": "user_saved", "q": "Shabbat"})

        assert response.status_code == 200
        assert response.json()["conversations"][0]["sessionId"] == "sess_saved"

    def test_loads_conversation_messages(self, client):
        self._create_saved_conversation()

        response = client.get("/api/conversations/sess_saved", {"userId": "user_saved"})

        assert response.status_code == 200
        body = response.json()
        assert body["conversation"]["sessionId"] == "sess_saved"
        assert [message["role"] for message in body["messages"]] == ["user", "assistant"]

    def test_renames_conversation(self, client):
        self._create_saved_conversation()

        response = client.patch(
            "/api/conversations/sess_saved",
            {"userId": "user_saved", "title": "Renamed"},
            format="json",
        )

        assert response.status_code == 200
        assert response.json()["conversation"]["title"] == "Renamed"
        assert ChatSession.objects.get(session_id="sess_saved").title == "Renamed"

    def test_deletes_conversation_permanently(self, client):
        self._create_saved_conversation()

        response = client.delete("/api/conversations/sess_saved?userId=user_saved")

        assert response.status_code == 204
        assert not ChatSession.objects.filter(session_id="sess_saved").exists()
        assert not ChatMessage.objects.filter(session_id="sess_saved").exists()
