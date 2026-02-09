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


class PromptSlugsSerializer(serializers.Serializer):
    """Optional core prompt slug override for Braintrust."""

    corePromptSlug = serializers.CharField(max_length=200, required=False, allow_blank=True)


class ChatRequestSerializer(serializers.Serializer):
    """Incoming chat message from client."""

    userId = serializers.CharField(max_length=512)
    sessionId = serializers.CharField(max_length=100)
    messageId = serializers.CharField(max_length=100)
    timestamp = serializers.DateTimeField()
    text = serializers.CharField(max_length=10000)
    context = MessageContextSerializer(required=False)
    promptSlugs = PromptSlugsSerializer(required=False)


class FeedbackRequestSerializer(serializers.Serializer):
    """User feedback payload for Braintrust logging."""

    traceId = serializers.CharField(max_length=200)
    score = serializers.FloatField(min_value=0.0, max_value=1.0)
    comment = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    feedbackReason = serializers.CharField(max_length=200, required=False, allow_blank=True)
    userId = serializers.CharField(max_length=512, required=False, allow_blank=True)
    sessionId = serializers.CharField(max_length=100, required=False, allow_blank=True)
    messageId = serializers.CharField(max_length=100, required=False, allow_blank=True)


class AnthropicRequestSerializer(serializers.Serializer):
    """Anthropic Messages API request format."""

    model = serializers.CharField(max_length=100, required=False)
    max_tokens = serializers.IntegerField(required=False)
    messages = serializers.ListField(child=serializers.DictField(), min_length=1)
    metadata = serializers.DictField(required=False)


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
