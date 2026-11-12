"""
Chat models for message persistence and metadata.
"""

import uuid

from django.db import models


class ChatSession(models.Model):
    """Tracks chat sessions with summaries and metadata."""

    session_id = models.CharField(max_length=100, unique=True, db_index=True)
    user_id = models.CharField(max_length=100, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    last_activity = models.DateTimeField(auto_now=True)

    message_count = models.IntegerField(default=0)
    turn_count = models.IntegerField(default=0)

    title = models.CharField(max_length=64, blank=True, default="")
    title_updated_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    # Legacy flow state (unused in v2 agent flow)
    current_flow = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Current conversation label (optional)",
    )

    # Rolling conversation summary for agent context
    conversation_summary = models.TextField(
        blank=True, default="", help_text="Rolling summary of conversation for agent context"
    )
    summary_updated_at = models.DateTimeField(null=True, blank=True)

    # Aggregate token usage for the session
    total_input_tokens = models.IntegerField(default=0)
    total_output_tokens = models.IntegerField(default=0)
    total_tool_calls = models.IntegerField(default=0)
    total_cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0, null=True)

    # Session metadata
    user_locale = models.CharField(max_length=10, blank=True, default="")
    user_type = models.CharField(max_length=50, blank=True, default="")

    class Meta:
        ordering = ["-last_activity"]

    def __str__(self):
        return f"Session {self.session_id} ({self.user_id})"


class ConversationSummary(models.Model):
    """
    Structured, rolling summary for a chat session.

    Updated by an AI summarizer and used as compact context for future turns.
    """

    session = models.OneToOneField(ChatSession, on_delete=models.CASCADE, related_name="summary")

    text = models.TextField(blank=True, default="")
    current_topic = models.CharField(max_length=255, blank=True, default="")
    user_intent = models.TextField(blank=True, default="")
    flow = models.CharField(max_length=20, blank=True, default="")

    texts_referenced = models.JSONField(default=list)
    topics_discussed = models.JSONField(default=list)
    people_mentioned = models.JSONField(default=list)

    halachic_domain = models.TextField(blank=True, default="")
    constraints = models.JSONField(default=list)
    safety_flags = models.JSONField(default=list)

    turn_count = models.IntegerField(default=0)
    last_updated = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-last_updated", "-created_at"]

    def __str__(self):
        return f"Summary {self.session.session_id[:12]}... ({self.turn_count} turns)"

    def to_prompt_text(self) -> str:
        """Convert to a compact text block for prompt context."""
        parts = []

        if self.text:
            parts.append(f"Summary: {self.text}")
        if self.current_topic:
            parts.append(f"Current Topic: {self.current_topic}")
        if self.user_intent:
            parts.append(f"User Intent: {self.user_intent}")
        if self.texts_referenced:
            parts.append(f"Texts: {', '.join(self.texts_referenced[:5])}")
        if self.topics_discussed:
            parts.append(f"Topics: {', '.join(self.topics_discussed[:5])}")
        if self.people_mentioned:
            parts.append(f"People: {', '.join(self.people_mentioned[:5])}")
        if self.halachic_domain:
            parts.append(f"Halachic Domain: {self.halachic_domain}")
        if self.constraints:
            parts.append(f"Constraints: {', '.join(self.constraints)}")
        if self.safety_flags:
            parts.append(f"Safety Flags: {', '.join(self.safety_flags)}")

        return "\n".join(parts)

    def to_metadata(self) -> dict:
        """Convert to metadata-friendly dictionary."""
        return {
            "text": self.text,
            "current_topic": self.current_topic,
            "user_intent": self.user_intent,
            "texts_referenced": self.texts_referenced,
            "topics_discussed": self.topics_discussed,
            "people_mentioned": self.people_mentioned,
            "halachic_domain": self.halachic_domain,
            "constraints": self.constraints,
            "safety_flags": self.safety_flags,
            "turn_count": self.turn_count,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }


class RouteDecision(models.Model):
    """
    Legacy routing decision record (kept for historical analytics).

    Provides audit trail and analytics for:
    - Flow classification accuracy
    - Guardrail triggering
    - Prompt/tool selection
    """

    class Flow(models.TextChoices):
        TRANSLATION = "TRANSLATION", "Translation"
        DISCOVERY = "DISCOVERY", "Discovery"
        DEEP_ENGAGEMENT = "DEEP_ENGAGEMENT", "Deep Engagement"
        REFUSE = "REFUSE", "Refuse/Guardrail"

    class SessionAction(models.TextChoices):
        CONTINUE = "CONTINUE", "Continue"
        SWITCH_FLOW = "SWITCH_FLOW", "Switch Flow"
        END = "END", "End"

    # Identifiers
    decision_id = models.CharField(max_length=100, unique=True, db_index=True)
    session_id = models.CharField(max_length=100, db_index=True)
    turn_id = models.CharField(max_length=100, db_index=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)

    # Router input
    user_message = models.TextField()
    conversation_summary_used = models.TextField(blank=True, default="")
    previous_flow = models.CharField(max_length=20, blank=True, default="")

    # Router output
    flow = models.CharField(max_length=20, choices=Flow.choices)
    confidence = models.FloatField(default=0.0)
    reason_codes = models.JSONField(default=list, help_text="List of reason codes for the decision")

    # Prompt selection
    core_prompt_id = models.CharField(max_length=100, blank=True, default="")
    core_prompt_version = models.CharField(max_length=50, blank=True, default="")
    flow_prompt_id = models.CharField(max_length=100, blank=True, default="")
    flow_prompt_version = models.CharField(max_length=50, blank=True, default="")

    # Tool selection
    tools_attached = models.JSONField(
        default=list, help_text="List of tool names attached for this turn"
    )

    # Session action
    session_action = models.CharField(
        max_length=20, choices=SessionAction.choices, default=SessionAction.CONTINUE
    )

    # Safety/guardrails
    safety_allowed = models.BooleanField(default=True)
    refusal_message = models.TextField(blank=True, default="")
    guardrail_triggered = models.JSONField(default=list, help_text="List of guardrails triggered")

    # Performance
    router_latency_ms = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["session_id", "created_at"]),
            models.Index(fields=["flow", "created_at"]),
        ]

    def __str__(self):
        return f"Route {self.decision_id}: {self.flow} ({', '.join(self.reason_codes[:3])})"

    @classmethod
    def generate_decision_id(cls):
        """Generate a unique decision ID."""
        return f"dec_{uuid.uuid4().hex[:16]}"


class ChatMessage(models.Model):
    """Stores all chat messages for analytics and debugging."""

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"

    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        REFUSED = "refused", "Refused"

    class ProcessingState(models.TextChoices):
        STARTED = "started", "Started"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLING = "cancelling", "Cancelling"
        CANCELLED = "cancelled", "Cancelled"

    class MessageKind(models.TextChoices):
        # Pre-tool-use narration the model wrote before its real answer (e.g.
        # "Let me check the calendar..."). Several of these can precede the
        # final_response row in the same turn.
        NARRATION = "narration", "Narration"
        FINAL_RESPONSE = "final_response", "Final response"

    # Identifiers
    message_id = models.CharField(max_length=100, unique=True, db_index=True)
    session_id = models.CharField(max_length=100, db_index=True)
    user_id = models.CharField(max_length=100, db_index=True)
    turn_id = models.CharField(max_length=100, db_index=True, blank=True, default="")

    # Link to route decision for this turn
    route_decision = models.ForeignKey(
        RouteDecision, on_delete=models.SET_NULL, null=True, blank=True, related_name="messages"
    )

    # Message content
    role = models.CharField(max_length=10, choices=Role.choices)
    content = models.TextField()

    # Timestamps
    client_timestamp = models.DateTimeField(null=True, blank=True)
    server_timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    # For user messages: link to the assistant response
    response_message = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="request_message"
    )

    # Metadata
    # Blank for user messages; for assistant messages, distinguishes a
    # narration row from the turn's one final_response row.
    message_kind = models.CharField(
        max_length=20, choices=MessageKind.choices, blank=True, default=""
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SUCCESS)
    # A logged-out visitor's answer that doesn't use up a free answer (see anon_access.py)
    free_answer = models.BooleanField(default=False)
    latency_ms = models.IntegerField(null=True, blank=True)
    processing_state = models.CharField(
        max_length=20, choices=ProcessingState.choices, blank=True, default=""
    )
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processing_heartbeat_at = models.DateTimeField(null=True, blank=True)
    processing_finished_at = models.DateTimeField(null=True, blank=True)
    processing_error = models.TextField(blank=True, default="")

    # Context from client
    page_url = models.URLField(max_length=2000, blank=True, default="")
    locale = models.CharField(max_length=10, blank=True, default="")
    client_version = models.CharField(max_length=20, blank=True, default="")

    # Flow context (denormalized for easy querying)
    flow = models.CharField(max_length=20, blank=True, default="")

    # Agent metadata (for assistant messages)
    model_name = models.CharField(max_length=100, blank=True, default="")
    llm_calls = models.IntegerField(null=True, blank=True)
    tool_calls_count = models.IntegerField(null=True, blank=True)
    tool_calls_data = models.JSONField(null=True, blank=True)  # List of tool calls
    appetizer_data = models.JSONField(
        null=True,
        blank=True,
        help_text="Prompt-level topic appetizer metadata displayed in chat history",
    )
    input_tokens = models.IntegerField(null=True, blank=True)
    output_tokens = models.IntegerField(null=True, blank=True)
    cache_creation_tokens = models.IntegerField(null=True, blank=True)
    cache_read_tokens = models.IntegerField(null=True, blank=True)
    total_cost_usd = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)

    class Meta:
        ordering = ["server_timestamp"]
        indexes = [
            models.Index(fields=["session_id", "server_timestamp"]),
            models.Index(fields=["user_id", "server_timestamp"]),
            models.Index(fields=["turn_id"]),
        ]

    def __str__(self):
        return f"{self.role}: {self.content[:50]}..."

    @classmethod
    def generate_message_id(cls):
        """Generate a unique message ID."""
        return f"msg_{uuid.uuid4().hex[:16]}"

    @classmethod
    def generate_turn_id(cls):
        """Generate a unique turn ID."""
        return f"turn_{uuid.uuid4().hex[:16]}"


def new_proposal_id() -> str:
    """Kept only because an applied migration (0016) refers to it."""
    return uuid.uuid4().hex


class PersonalizationSettings(models.Model):
    """One row per signed-in user: Instructions, the Profile switches and the writer's watermark."""

    MAX_INSTRUCTIONS = 300

    user_id = models.CharField(max_length=100, unique=True)
    # Written by the user; the 300-character cap is a column limit as well as an API check.
    instructions_text = models.CharField(max_length=300, blank=True, default="")
    instructions_enabled = models.BooleanField(default=True)
    instructions_updated_at = models.DateTimeField(null=True, blank=True)
    profile_enabled = models.BooleanField(default=True)
    profile_updated_at = models.DateTimeField(null=True, blank=True)
    # "la" (the writer) or "you_la" (the user asked LA to edit); None before the first write.
    profile_updated_by = models.CharField(max_length=10, null=True, blank=True)
    # Bumped by every applied write, so a write computed from an older Profile is refused.
    profile_revision = models.PositiveIntegerField(default=0)
    # Time of the newest prompt already processed (the watermark) and of the last Clear.
    processed_through = models.DateTimeField(null=True, blank=True)
    cleared_at = models.DateTimeField(null=True, blank=True)
    notice_pending = models.BooleanField(default=False)
    # Lab-only testing switches.
    show_plain_response = models.BooleanField(default=False)
    update_every_message = models.BooleanField(default=False)


class ProfileSection(models.Model):
    """One section of a user's Profile: a core slot or a dynamic section LA opened."""

    class Kind(models.TextChoices):
        CORE = "core", "Core"
        DYNAMIC = "dynamic", "Dynamic"

    class Origin(models.TextChoices):
        STATED = "stated", "Stated"
        INFERRED = "inferred", "Inferred"

    user_id = models.CharField(max_length=100, db_index=True)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    # The core slot key; null for a dynamic section.
    key = models.CharField(max_length=40, null=True, blank=True)
    heading = models.CharField(max_length=100)
    body = models.TextField()
    origin = models.CharField(max_length=10, choices=Origin.choices, default=Origin.INFERRED)
    last_supported_at = models.DateTimeField()
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["user_id", "heading"], name="uniq_profile_section_heading"
            )
        ]


class ProfileWriterRun(models.Model):
    """One run of the Profile job, kept for review and for a later judge."""

    user_id = models.CharField(max_length=100, db_index=True)
    trigger = models.CharField(max_length=20)
    window = models.CharField(max_length=10, default="session")
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    watermark_from = models.DateTimeField(null=True, blank=True)
    watermark_to = models.DateTimeField(null=True, blank=True)
    prompt_ids = models.JSONField(default=list)
    # The text fields below are blanked when the user clears their Profile.
    extraction = models.TextField(blank=True, default="")
    profile_before = models.TextField(blank=True, default="")
    profile_after = models.TextField(blank=True, default="")
    chapter_hashes = models.JSONField(default=dict)
    model = models.CharField(max_length=100, blank=True, default="")
    input_tokens = models.IntegerField(null=True, blank=True)
    output_tokens = models.IntegerField(null=True, blank=True)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    # applied | no_change | rejected | failed
    status = models.CharField(max_length=12)
    failed_check = models.CharField(max_length=40, blank=True, default="")
    reason = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        indexes = [models.Index(fields=["user_id", "started_at"])]


class LabQuota(models.Model):
    """A lab cost counter: calls of one kind by one learner (or "__all__") in one time window."""

    scope = models.CharField(max_length=16)  # "job" or "seed"
    key = models.CharField(max_length=100)
    window_start = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "key", "window_start"], name="lab_quota_scope_key_window"
            )
        ]


class LabJobLock(models.Model):
    """Single-flight marker for one learner's job run; a stale start (over 300 s) is ignored."""

    user_id = models.CharField(max_length=100, unique=True)
    running = models.BooleanField(default=False)
    started_at = models.DateTimeField(null=True)
