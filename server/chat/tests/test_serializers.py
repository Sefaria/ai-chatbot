"""Tests for API serializers - request validation, response formatting."""

import pytest
from django.utils import timezone

from chat.models import ChatMessage
from chat.serializers import (
    MAX_ANTHROPIC_MESSAGES,
    MAX_ANTHROPIC_TEXT_CHARS,
    AnthropicRequestSerializer,
    ChatRequestSerializer,
    ChatResponseSerializer,
    HistoryMessageSerializer,
    MessageContextSerializer,
)


class TestMessageContextSerializer:
    """Test MessageContextSerializer."""

    @pytest.mark.parametrize(
        "data,is_valid",
        [
            (
                {
                    "pageUrl": "https://example.com/page",
                    "locale": "en-US",
                    "clientVersion": "1.0.0",
                },
                True,
            ),
            ({}, True),
            ({"locale": "he"}, True),
            ({"pageUrl": "", "locale": "", "clientVersion": ""}, True),
            ({"labs": True}, True),
            ({"pageUrl": "not-a-url"}, False),
        ],
    )
    def test_context_validation(self, data, is_valid):
        serializer = MessageContextSerializer(data=data)
        assert serializer.is_valid() == is_valid

    def test_valid_context_values(self):
        data = {"pageUrl": "https://example.com/page", "locale": "he", "labs": True}
        serializer = MessageContextSerializer(data=data)
        assert serializer.is_valid()
        assert serializer.validated_data["pageUrl"] == "https://example.com/page"
        assert serializer.validated_data["locale"] == "he"
        assert serializer.validated_data["labs"] is True

    def test_invalid_url_error(self):
        serializer = MessageContextSerializer(data={"pageUrl": "not-a-url"})
        assert not serializer.is_valid()
        assert "pageUrl" in serializer.errors


class TestChatRequestSerializer:
    """Test ChatRequestSerializer."""

    @pytest.fixture
    def valid_request_data(self):
        return {
            "userId": "user_123",
            "sessionId": "sess_456",
            "messageId": "msg_789",
            "timestamp": "2024-01-15T10:30:00Z",
            "text": "What is the halacha about Shabbat?",
        }

    def test_valid_request(self, valid_request_data):
        serializer = ChatRequestSerializer(data=valid_request_data)
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["userId"] == "user_123"
        assert serializer.validated_data["text"] == "What is the halacha about Shabbat?"

    def test_valid_request_with_context(self, valid_request_data):
        valid_request_data["context"] = {"pageUrl": "https://example.com", "locale": "en"}
        serializer = ChatRequestSerializer(data=valid_request_data)
        assert serializer.is_valid()
        assert serializer.validated_data["context"]["locale"] == "en"

    def test_missing_required_fields(self):
        serializer = ChatRequestSerializer(data={"userId": "user_123"})
        assert not serializer.is_valid()
        for field in ["sessionId", "messageId", "timestamp", "text"]:
            assert field in serializer.errors

    @pytest.mark.parametrize(
        "field,invalid_value,error_field",
        [
            ("userId", "", "userId"),
            ("text", "x" * 10001, "text"),
            ("timestamp", "not-a-timestamp", "timestamp"),
            ("userId", "u" * 513, "userId"),
        ],
    )
    def test_field_validation_errors(self, valid_request_data, field, invalid_value, error_field):
        valid_request_data[field] = invalid_value
        serializer = ChatRequestSerializer(data=valid_request_data)
        assert not serializer.is_valid()
        assert error_field in serializer.errors

    def test_text_at_max_length(self, valid_request_data):
        valid_request_data["text"] = "x" * 10000
        serializer = ChatRequestSerializer(data=valid_request_data)
        assert serializer.is_valid()

    @pytest.mark.parametrize(
        "timestamp",
        [
            "2024-01-15T10:30:00Z",
            "2024-01-15T10:30:00.000Z",
            "2024-01-15T10:30:00+00:00",
        ],
    )
    def test_various_timestamp_formats(self, valid_request_data, timestamp):
        valid_request_data["timestamp"] = timestamp
        serializer = ChatRequestSerializer(data=valid_request_data)
        assert serializer.is_valid(), f"Failed for timestamp: {timestamp}"

    def test_context_optional(self, valid_request_data):
        serializer = ChatRequestSerializer(data=valid_request_data)
        assert serializer.is_valid()


class TestChatResponseSerializer:
    """Test ChatResponseSerializer."""

    def test_valid_response(self):
        data = {
            "messageId": "msg_response_123",
            "sessionId": "sess_456",
            "timestamp": timezone.now(),
            "markdown": "According to halacha, this is permitted.",
        }
        serializer = ChatResponseSerializer(data=data)
        assert serializer.is_valid()

    def test_missing_fields(self):
        serializer = ChatResponseSerializer(data={"messageId": "msg_123"})
        assert not serializer.is_valid()


@pytest.mark.django_db
class TestHistoryMessageSerializer:
    """Test HistoryMessageSerializer."""

    @pytest.fixture
    def user_message(self):
        return ChatMessage.objects.create(
            message_id="msg_hist_user",
            session_id="sess_hist",
            user_id="user_hist",
            role="user",
            content="What is Shabbat?",
        )

    def test_serialize_user_message(self, user_message):
        data = HistoryMessageSerializer(user_message).data
        assert data["messageId"] == "msg_hist_user"
        assert data["sessionId"] == "sess_hist"
        assert data["userId"] == "user_hist"
        assert data["role"] == "user"
        assert data["content"] == "What is Shabbat?"
        assert "timestamp" in data

    def test_serialize_assistant_message(self):
        message = ChatMessage.objects.create(
            message_id="msg_hist_assistant",
            session_id="sess_hist",
            user_id="user_hist",
            role="assistant",
            content="Shabbat is the Jewish day of rest...",
        )
        data = HistoryMessageSerializer(message).data
        assert data["role"] == "assistant"
        assert "Shabbat" in data["content"]

    def test_serialize_multiple_messages(self):
        for i in range(3):
            ChatMessage.objects.create(
                message_id=f"msg_multi_{i}",
                session_id="sess_multi",
                user_id="user_multi",
                role="user" if i % 2 == 0 else "assistant",
                content=f"Message {i}",
            )

        messages = ChatMessage.objects.filter(session_id="sess_multi")
        data = HistoryMessageSerializer(messages, many=True).data
        assert len(data) == 3
        assert data[0]["messageId"] == "msg_multi_0"
        assert data[1]["messageId"] == "msg_multi_1"

    def test_timestamp_field_mapping(self, user_message):
        data = HistoryMessageSerializer(user_message).data
        assert "timestamp" in data
        assert "server_timestamp" not in data

    def test_only_specified_fields_included(self):
        message = ChatMessage.objects.create(
            message_id="msg_fields",
            session_id="sess_fields",
            user_id="user_fields",
            role="user",
            content="test",
            flow="DEEP_ENGAGEMENT",
            latency_ms=100,
            input_tokens=50,
        )
        data = HistoryMessageSerializer(message).data

        excluded_fields = ["flow", "latency_ms", "input_tokens"]
        for field in excluded_fields:
            assert field not in data

        expected_fields = {
            "messageId",
            "sessionId",
            "userId",
            "role",
            "content",
            "timestamp",
            "status",
            "pageUrl",
            "responseMessageId",
            "appetizerData",
            "toolCalls",
            "processingState",
        }
        assert set(data.keys()) == expected_fields


class TestAnthropicRequestSerializer:
    """Test AnthropicRequestSerializer size limits on the messages list."""

    @staticmethod
    def _user_messages(count, text="hi"):
        return [
            {"role": "user" if i % 2 == 0 else "assistant", "content": text} for i in range(count)
        ]

    def test_valid_request(self):
        serializer = AnthropicRequestSerializer(
            data={"messages": [{"role": "user", "content": "Hello"}]}
        )
        assert serializer.is_valid(), serializer.errors

    def test_messages_at_max_count_is_valid(self):
        serializer = AnthropicRequestSerializer(
            data={"messages": self._user_messages(MAX_ANTHROPIC_MESSAGES)}
        )
        assert serializer.is_valid(), serializer.errors

    def test_messages_over_max_count_is_rejected(self):
        serializer = AnthropicRequestSerializer(
            data={"messages": self._user_messages(MAX_ANTHROPIC_MESSAGES + 1)}
        )
        assert not serializer.is_valid()
        assert "messages" in serializer.errors

    def test_empty_messages_is_rejected(self):
        serializer = AnthropicRequestSerializer(data={"messages": []})
        assert not serializer.is_valid()
        assert "messages" in serializer.errors

    def test_total_text_at_limit_is_valid(self):
        # Split the budget across messages, string and block content alike.
        half = MAX_ANTHROPIC_TEXT_CHARS // 2
        messages = [
            {"role": "user", "content": "x" * half},
            {
                "role": "user",
                "content": [{"type": "text", "text": "y" * (MAX_ANTHROPIC_TEXT_CHARS - half)}],
            },
        ]
        serializer = AnthropicRequestSerializer(data={"messages": messages})
        assert serializer.is_valid(), serializer.errors

    def test_total_text_over_limit_is_rejected(self):
        messages = [
            {"role": "user", "content": "x" * MAX_ANTHROPIC_TEXT_CHARS},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": [{"type": "text", "text": "y"}]},
        ]
        serializer = AnthropicRequestSerializer(data={"messages": messages})
        assert not serializer.is_valid()
        assert "messages" in serializer.errors

    def test_total_text_counts_text_blocks_only(self):
        # Non-text blocks and non-string text values contribute nothing to the total.
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"data": "z" * (MAX_ANTHROPIC_TEXT_CHARS * 2)}}
                ],
            },
            {"role": "user", "content": [{"type": "text", "text": 123}]},
            {"role": "user", "content": 42},
        ]
        serializer = AnthropicRequestSerializer(data={"messages": messages})
        assert serializer.is_valid(), serializer.errors
