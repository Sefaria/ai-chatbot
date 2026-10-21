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
    origin = serializers.CharField(max_length=100, required=False, allow_blank=True)
    isStaff = serializers.BooleanField(required=False, default=False)
    labs = serializers.BooleanField(required=False, default=False)
    forceStreamBreakBeforeFinal = serializers.BooleanField(required=False, default=False)


class PromptSlugsSerializer(serializers.Serializer):
    """Optional core prompt slug override for Braintrust."""

    corePromptSlug = serializers.CharField(max_length=200, required=False, allow_blank=True)
    labs = serializers.BooleanField(required=False, default=False)


class ChatRequestSerializer(serializers.Serializer):
    """Incoming chat message from client."""

    userId = serializers.CharField(max_length=512)
    sessionId = serializers.CharField(max_length=100)
    messageId = serializers.CharField(max_length=100)
    timestamp = serializers.DateTimeField()
    text = serializers.CharField(max_length=10000)
    context = MessageContextSerializer(required=False)
    promptSlugs = PromptSlugsSerializer(required=False)
    isLoadTest = serializers.BooleanField(required=False, default=False)


class FeedbackRequestSerializer(serializers.Serializer):
    """User feedback payload for Braintrust logging."""

    SCORE_CHOICES = [("up", "Thumbs up"), ("down", "Thumbs down")]

    traceId = serializers.CharField(max_length=200)
    score = serializers.ChoiceField(choices=SCORE_CHOICES)
    userId = serializers.CharField(max_length=512)
    sessionId = serializers.CharField(max_length=100)
    messageId = serializers.CharField(max_length=100)

    # non-required fields
    comment = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    feedbackReason = serializers.CharField(max_length=200, required=False, allow_blank=True)


class RecoveryRequestSerializer(serializers.Serializer):
    """Lookup request for a streamed response that may have been persisted already."""

    userId = serializers.CharField(max_length=512)
    sessionId = serializers.CharField(max_length=100)
    messageId = serializers.CharField(max_length=100)


class CancelRequestSerializer(serializers.Serializer):
    """Request to abandon an in-flight turn."""

    userId = serializers.CharField(max_length=512)
    sessionId = serializers.CharField(max_length=100)
    messageId = serializers.CharField(max_length=100)


class ClientStreamEventSerializer(serializers.Serializer):
    """Browser-side telemetry for stream failures and recoveries."""

    userId = serializers.CharField(max_length=512)
    sessionId = serializers.CharField(max_length=100)
    messageId = serializers.CharField(max_length=100)
    timestamp = serializers.DateTimeField()
    event = serializers.CharField(max_length=100)
    error = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    context = MessageContextSerializer(required=False)


# Generous ceilings on the Anthropic-format endpoint: bound the work per request
# without constraining real conversations.
MAX_ANTHROPIC_MESSAGES = 200
MAX_ANTHROPIC_TEXT_CHARS = 200_000


def _message_text_length(content) -> int:
    """Length of the text carried by one message's content (string or text blocks)."""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, str):
                total += len(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    total += len(text)
        return total
    return 0


class AnthropicRequestSerializer(serializers.Serializer):
    """Anthropic Messages API request format."""

    model = serializers.CharField(max_length=100, required=False)
    max_tokens = serializers.IntegerField(required=False)
    messages = serializers.ListField(
        child=serializers.DictField(),
        min_length=1,
        max_length=MAX_ANTHROPIC_MESSAGES,
    )
    metadata = serializers.DictField(required=False)

    def validate_messages(self, messages):
        total_chars = sum(_message_text_length(msg.get("content")) for msg in messages)
        if total_chars > MAX_ANTHROPIC_TEXT_CHARS:
            raise serializers.ValidationError(
                f"Total message text exceeds {MAX_ANTHROPIC_TEXT_CHARS} characters."
            )
        return messages


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
    pageUrl = serializers.CharField(source="page_url")
    responseMessageId = serializers.SerializerMethodField()
    appetizerData = serializers.JSONField(source="appetizer_data")
    toolCalls = serializers.JSONField(source="tool_calls_data")

    processingState = serializers.CharField(source="processing_state")

    class Meta:
        model = ChatMessage
        fields = [
            "messageId",
            "sessionId",
            "userId",
            "role",
            "content",
            "timestamp",
            "processingState",
            "status",
            "pageUrl",
            "responseMessageId",
            "appetizerData",
            "toolCalls",
        ]

    def get_responseMessageId(self, obj):
        return obj.response_message.message_id if obj.response_message else None


class HistoryResponseSerializer(serializers.Serializer):
    """Response format for history endpoint."""

    messages = HistoryMessageSerializer(many=True)
    hasMore = serializers.BooleanField()
