"""
Serializers for chat API.
"""

from rest_framework import serializers

from .models import ChatMessage


class LenientFlowField(serializers.CharField):
    """A flow hint that can never fail the request.

    The flow only selects a prompt. A client that sends junk — a stale build, a
    wrong type, an over-long string — should still get a normal answer rather
    than a 400, so anything unusable degrades to blank here and is dropped
    downstream by chat.V2.agent.flows.normalize_flow.
    """

    MAX_LENGTH = 40

    def to_internal_value(self, data):
        if not isinstance(data, str):
            return ""
        return super().to_internal_value(data[: self.MAX_LENGTH])


class MessageContextSerializer(serializers.Serializer):
    """Context information sent with each message."""

    pageUrl = serializers.URLField(required=False, allow_blank=True)
    locale = serializers.CharField(max_length=10, required=False, allow_blank=True)
    clientVersion = serializers.CharField(max_length=20, required=False, allow_blank=True)
    origin = serializers.CharField(max_length=100, required=False, allow_blank=True)
    isStaff = serializers.BooleanField(required=False, default=False)
    labs = serializers.BooleanField(required=False, default=False)
    forceStreamBreakBeforeFinal = serializers.BooleanField(required=False, default=False)
    # Client-initiated flow (e.g. 'report_issue'). Validated against the known
    # flows in chat.V2.agent.flows, not here — an unrecognised value is dropped
    # rather than rejected, so a stale client still gets a normal answer.
    flow = LenientFlowField(required=False, allow_blank=True)


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


class ClientStreamEventSerializer(serializers.Serializer):
    """Browser-side telemetry for stream failures and recoveries."""

    userId = serializers.CharField(max_length=512)
    sessionId = serializers.CharField(max_length=100)
    messageId = serializers.CharField(max_length=100)
    timestamp = serializers.DateTimeField()
    event = serializers.CharField(max_length=100)
    error = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    context = MessageContextSerializer(required=False)


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
    pageUrl = serializers.CharField(source="page_url")
    responseMessageId = serializers.SerializerMethodField()
    appetizerData = serializers.JSONField(source="appetizer_data")
    toolCalls = serializers.JSONField(source="tool_calls_data")

    class Meta:
        model = ChatMessage
        fields = [
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
        ]

    def get_responseMessageId(self, obj):
        return obj.response_message.message_id if obj.response_message else None


class HistoryResponseSerializer(serializers.Serializer):
    """Response format for history endpoint."""

    messages = HistoryMessageSerializer(many=True)
    hasMore = serializers.BooleanField()
