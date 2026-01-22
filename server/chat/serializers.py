"""
Serializers for chat API.
"""

from rest_framework import serializers

from .models import ChatMessage


class MessageContextSerializer(serializers.Serializer):
    """Context information sent with each message."""

    pageUrl = serializers.URLField(required=False, allow_blank=True)
    locale = serializers.CharField(max_length=10, required=False, allow_blank=True)
    clientVersion = serializers.CharField(max_length=20, required=False, allow_blank=True)


class ChatRequestSerializer(serializers.Serializer):
    """Incoming chat message from client."""

    userId = serializers.CharField(max_length=100)
    sessionId = serializers.CharField(max_length=100)
    messageId = serializers.CharField(max_length=100)
    timestamp = serializers.DateTimeField()
    text = serializers.CharField(max_length=10000)
    context = MessageContextSerializer(required=False)


class ChatResponseSerializer(serializers.Serializer):
    """Response to client after processing message."""

    messageId = serializers.CharField()
    sessionId = serializers.CharField()
    timestamp = serializers.DateTimeField()
    markdown = serializers.CharField()


class HistoryMessageSerializer(serializers.ModelSerializer):
    """Message format for history endpoint."""

    messageId = serializers.CharField(source="message_id")
    sessionId = serializers.CharField(source="session_id")
    userId = serializers.CharField(source="user_id")
    timestamp = serializers.DateTimeField(source="server_timestamp")

    class Meta:
        model = ChatMessage
        fields = ["messageId", "sessionId", "userId", "role", "content", "timestamp"]


class HistoryResponseSerializer(serializers.Serializer):
    """Response format for history endpoint."""

    messages = HistoryMessageSerializer(many=True)
    hasMore = serializers.BooleanField()


class OpenAIMessageSerializer(serializers.Serializer):
    """Single message in OpenAI chat format."""

    role = serializers.ChoiceField(choices=["system", "user", "assistant"])
    content = serializers.CharField(max_length=50000)


class OpenAIChatRequestSerializer(serializers.Serializer):
    """OpenAI-compatible chat completion request."""

    model = serializers.CharField(max_length=100, default="sefaria-agent")
    messages = serializers.ListField(child=OpenAIMessageSerializer(), min_length=1, max_length=100)
