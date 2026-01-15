"""
Tests for API serializers - request validation, response formatting.
"""

import pytest
from datetime import datetime
from django.utils import timezone

from chat.serializers import (
    ChatRequestSerializer,
    ChatResponseSerializer,
    MessageContextSerializer,
    HistoryMessageSerializer,
)
from chat.models import ChatMessage


class TestMessageContextSerializer:
    """Test MessageContextSerializer."""

    def test_valid_context(self):
        """Test valid context data."""
        data = {
            "pageUrl": "https://example.com/page",
            "locale": "en-US",
            "clientVersion": "1.0.0",
        }
        serializer = MessageContextSerializer(data=data)
        assert serializer.is_valid()
        assert serializer.validated_data["pageUrl"] == "https://example.com/page"

    def test_empty_context(self):
        """Test empty context is valid."""
        serializer = MessageContextSerializer(data={})
        assert serializer.is_valid()

    def test_partial_context(self):
        """Test partial context data."""
        data = {"locale": "he"}
        serializer = MessageContextSerializer(data=data)
        assert serializer.is_valid()
        assert serializer.validated_data["locale"] == "he"

    def test_invalid_url(self):
        """Test invalid URL is rejected."""
        data = {"pageUrl": "not-a-url"}
        serializer = MessageContextSerializer(data=data)
        assert not serializer.is_valid()
        assert "pageUrl" in serializer.errors

    def test_blank_values_allowed(self):
        """Test blank values are allowed."""
        data = {"pageUrl": "", "locale": "", "clientVersion": ""}
        serializer = MessageContextSerializer(data=data)
        assert serializer.is_valid()


class TestChatRequestSerializer:
    """Test ChatRequestSerializer."""

    def test_valid_request(self):
        """Test valid chat request."""
        data = {
            "userId": "user_123",
            "sessionId": "sess_456",
            "messageId": "msg_789",
            "timestamp": "2024-01-15T10:30:00Z",
            "text": "What is the halacha about Shabbat?",
        }
        serializer = ChatRequestSerializer(data=data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["userId"] == "user_123"
        assert serializer.validated_data["text"] == "What is the halacha about Shabbat?"

    def test_valid_request_with_context(self):
        """Test valid request with context."""
        data = {
            "userId": "user_123",
            "sessionId": "sess_456",
            "messageId": "msg_789",
            "timestamp": "2024-01-15T10:30:00Z",
            "text": "Hello",
            "context": {
                "pageUrl": "https://example.com",
                "locale": "en",
            },
        }
        serializer = ChatRequestSerializer(data=data)
        assert serializer.is_valid()
        assert serializer.validated_data["context"]["locale"] == "en"

    def test_missing_required_fields(self):
        """Test missing required fields."""
        data = {"userId": "user_123"}
        serializer = ChatRequestSerializer(data=data)
        assert not serializer.is_valid()
        assert "sessionId" in serializer.errors
        assert "messageId" in serializer.errors
        assert "timestamp" in serializer.errors
        assert "text" in serializer.errors

    def test_empty_user_id(self):
        """Test empty userId is rejected."""
        data = {
            "userId": "",
            "sessionId": "sess",
            "messageId": "msg",
            "timestamp": "2024-01-15T10:30:00Z",
            "text": "test",
        }
        serializer = ChatRequestSerializer(data=data)
        assert not serializer.is_valid()
        assert "userId" in serializer.errors

    def test_text_max_length(self):
        """Test text max length validation."""
        data = {
            "userId": "user",
            "sessionId": "sess",
            "messageId": "msg",
            "timestamp": "2024-01-15T10:30:00Z",
            "text": "x" * 10001,  # Exceeds 10000 limit
        }
        serializer = ChatRequestSerializer(data=data)
        assert not serializer.is_valid()
        assert "text" in serializer.errors

    def test_text_at_max_length(self):
        """Test text at exactly max length."""
        data = {
            "userId": "user",
            "sessionId": "sess",
            "messageId": "msg",
            "timestamp": "2024-01-15T10:30:00Z",
            "text": "x" * 10000,  # Exactly at limit
        }
        serializer = ChatRequestSerializer(data=data)
        assert serializer.is_valid()

    def test_invalid_timestamp(self):
        """Test invalid timestamp format."""
        data = {
            "userId": "user",
            "sessionId": "sess",
            "messageId": "msg",
            "timestamp": "not-a-timestamp",
            "text": "test",
        }
        serializer = ChatRequestSerializer(data=data)
        assert not serializer.is_valid()
        assert "timestamp" in serializer.errors

    def test_various_timestamp_formats(self):
        """Test various valid timestamp formats."""
        timestamps = [
            "2024-01-15T10:30:00Z",
            "2024-01-15T10:30:00.000Z",
            "2024-01-15T10:30:00+00:00",
        ]
        for ts in timestamps:
            data = {
                "userId": "user",
                "sessionId": "sess",
                "messageId": "msg",
                "timestamp": ts,
                "text": "test",
            }
            serializer = ChatRequestSerializer(data=data)
            assert serializer.is_valid(), f"Failed for timestamp: {ts}"

    def test_context_optional(self):
        """Test context is optional."""
        data = {
            "userId": "user",
            "sessionId": "sess",
            "messageId": "msg",
            "timestamp": "2024-01-15T10:30:00Z",
            "text": "test",
        }
        serializer = ChatRequestSerializer(data=data)
        assert serializer.is_valid()

    def test_id_max_length(self):
        """Test ID field max length."""
        data = {
            "userId": "u" * 101,  # Exceeds 100
            "sessionId": "sess",
            "messageId": "msg",
            "timestamp": "2024-01-15T10:30:00Z",
            "text": "test",
        }
        serializer = ChatRequestSerializer(data=data)
        assert not serializer.is_valid()
        assert "userId" in serializer.errors


class TestChatResponseSerializer:
    """Test ChatResponseSerializer."""

    def test_valid_response(self):
        """Test valid response data."""
        data = {
            "messageId": "msg_response_123",
            "sessionId": "sess_456",
            "timestamp": timezone.now(),
            "markdown": "According to halacha, this is permitted.",
        }
        serializer = ChatResponseSerializer(data=data)
        assert serializer.is_valid()

    def test_missing_fields(self):
        """Test missing required fields."""
        data = {"messageId": "msg_123"}
        serializer = ChatResponseSerializer(data=data)
        assert not serializer.is_valid()


@pytest.mark.django_db
class TestHistoryMessageSerializer:
    """Test HistoryMessageSerializer."""

    def test_serialize_user_message(self):
        """Test serializing a user message."""
        message = ChatMessage.objects.create(
            message_id="msg_hist_user",
            session_id="sess_hist",
            user_id="user_hist",
            role="user",
            content="What is Shabbat?",
        )
        serializer = HistoryMessageSerializer(message)
        data = serializer.data

        assert data["messageId"] == "msg_hist_user"
        assert data["sessionId"] == "sess_hist"
        assert data["userId"] == "user_hist"
        assert data["role"] == "user"
        assert data["content"] == "What is Shabbat?"
        assert "timestamp" in data

    def test_serialize_assistant_message(self):
        """Test serializing an assistant message."""
        message = ChatMessage.objects.create(
            message_id="msg_hist_assistant",
            session_id="sess_hist",
            user_id="user_hist",
            role="assistant",
            content="Shabbat is the Jewish day of rest...",
        )
        serializer = HistoryMessageSerializer(message)
        data = serializer.data

        assert data["role"] == "assistant"
        assert "Shabbat" in data["content"]

    def test_serialize_multiple_messages(self):
        """Test serializing multiple messages."""
        for i in range(3):
            ChatMessage.objects.create(
                message_id=f"msg_multi_{i}",
                session_id="sess_multi",
                user_id="user_multi",
                role="user" if i % 2 == 0 else "assistant",
                content=f"Message {i}",
            )

        messages = ChatMessage.objects.filter(session_id="sess_multi")
        serializer = HistoryMessageSerializer(messages, many=True)
        data = serializer.data

        assert len(data) == 3
        assert data[0]["messageId"] == "msg_multi_0"
        assert data[1]["messageId"] == "msg_multi_1"

    def test_timestamp_field_mapping(self):
        """Test that server_timestamp maps to timestamp."""
        message = ChatMessage.objects.create(
            message_id="msg_ts",
            session_id="sess_ts",
            user_id="user_ts",
            role="user",
            content="test",
        )
        serializer = HistoryMessageSerializer(message)
        data = serializer.data

        # server_timestamp should be mapped to timestamp
        assert "timestamp" in data
        assert "server_timestamp" not in data

    def test_only_specified_fields_included(self):
        """Test that only specified fields are in output."""
        message = ChatMessage.objects.create(
            message_id="msg_fields",
            session_id="sess_fields",
            user_id="user_fields",
            role="user",
            content="test",
            flow="HALACHIC",
            latency_ms=100,
            input_tokens=50,
        )
        serializer = HistoryMessageSerializer(message)
        data = serializer.data

        # These should NOT be in the output
        assert "flow" not in data
        assert "latency_ms" not in data
        assert "input_tokens" not in data

        # These SHOULD be in the output
        expected_fields = {"messageId", "sessionId", "userId", "role", "content", "timestamp"}
        assert set(data.keys()) == expected_fields
