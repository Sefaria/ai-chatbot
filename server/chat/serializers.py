"""
Serializers for chat API.
"""

from rest_framework import serializers

from .models import ChatMessage, ChatSession


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
    status = serializers.CharField()
    traceId = serializers.SerializerMethodField()
    locationRef = serializers.SerializerMethodField()

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
            "traceId",
            "locationRef",
        ]

    def get_traceId(self, obj):
        return None

    def get_locationRef(self, obj):
        if obj.role != ChatMessage.Role.USER or not obj.page_url:
            return None
        return {"label": obj.page_url, "url": obj.page_url}


class HistoryResponseSerializer(serializers.Serializer):
    """Response format for history endpoint."""

    messages = HistoryMessageSerializer(many=True)
    hasMore = serializers.BooleanField()


class SavedConversationSerializer(serializers.ModelSerializer):
    """List item for saved conversations."""

    sessionId = serializers.CharField(source="session_id")
    title = serializers.SerializerMethodField()
    createdAt = serializers.DateTimeField(source="created_at")
    lastActivity = serializers.DateTimeField(source="last_activity")
    turnCount = serializers.IntegerField(source="turn_count")
    messageCount = serializers.IntegerField(source="message_count")
    totalTokens = serializers.SerializerMethodField()

    class Meta:
        model = ChatSession
        fields = [
            "sessionId",
            "title",
            "createdAt",
            "lastActivity",
            "turnCount",
            "messageCount",
            "totalTokens",
        ]

    def get_totalTokens(self, obj):
        return (obj.total_input_tokens or 0) + (obj.total_output_tokens or 0)

    def get_title(self, obj):
        if obj.title:
            return obj.title
        first_prompt = (
            ChatMessage.objects.filter(
                session_id=obj.session_id,
                user_id=obj.user_id,
                role=ChatMessage.Role.USER,
            )
            .order_by("server_timestamp")
            .values_list("content", flat=True)
            .first()
        )
        return " ".join((first_prompt or "").split())[:64]


class RenameConversationSerializer(serializers.Serializer):
    """Payload for renaming a saved conversation."""

    userId = serializers.CharField(max_length=512)
    title = serializers.CharField(max_length=64, allow_blank=False, trim_whitespace=True)
