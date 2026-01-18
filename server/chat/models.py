"""
Chat models for message persistence, routing, and logging.

Supports the routed Claude agent architecture with:
- Flow-based routing (HALACHIC, GENERAL, SEARCH, REFUSE)
- Conversation summaries for efficient context
- Route decisions per turn
- Tool call event logging
"""

import uuid

from django.db import models


class ChatSession(models.Model):
    """
    Tracks chat sessions with flow state and summaries.
    """

    session_id = models.CharField(max_length=100, unique=True, db_index=True)
    user_id = models.CharField(max_length=100, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    last_activity = models.DateTimeField(auto_now=True)

    message_count = models.IntegerField(default=0)
    turn_count = models.IntegerField(default=0)

    # Current flow state
    current_flow = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Current conversation flow (HALACHIC, GENERAL, SEARCH)",
    )

    # Rolling conversation summary for router context
    conversation_summary = models.TextField(
        blank=True, default="", help_text="Rolling summary of conversation for router context"
    )
    summary_updated_at = models.DateTimeField(null=True, blank=True)

    # Aggregate token usage for the session
    total_input_tokens = models.IntegerField(default=0)
    total_output_tokens = models.IntegerField(default=0)
    total_tool_calls = models.IntegerField(default=0)

    # Session metadata
    user_locale = models.CharField(max_length=10, blank=True, default="")
    user_type = models.CharField(max_length=50, blank=True, default="")

    class Meta:
        ordering = ["-last_activity"]

    def __str__(self):
        return f"Session {self.session_id} (user: {self.user_id}, flow: {self.current_flow})"


class RouteDecision(models.Model):
    """
    Records routing decisions for each turn.

    Provides audit trail and analytics for:
    - Flow classification accuracy
    - Guardrail triggering
    - Prompt/tool selection
    """

    class Flow(models.TextChoices):
        HALACHIC = "HALACHIC", "Halachic"
        GENERAL = "GENERAL", "General Learning"
        SEARCH = "SEARCH", "Search"
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
    """
    Stores all chat messages for analytics and debugging.
    """

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"

    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        REFUSED = "refused", "Refused"

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
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SUCCESS)
    latency_ms = models.IntegerField(null=True, blank=True)

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
    input_tokens = models.IntegerField(null=True, blank=True)
    output_tokens = models.IntegerField(null=True, blank=True)
    cache_creation_tokens = models.IntegerField(null=True, blank=True)
    cache_read_tokens = models.IntegerField(null=True, blank=True)

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


class ToolCallEvent(models.Model):
    """
    Records individual tool call events for Braintrust logging.

    Provides granular visibility into:
    - Tool usage patterns
    - Tool performance (latency, errors)
    - Tool result quality
    """

    # Identifiers (for Braintrust + LangSmith correlation)
    event_id = models.CharField(max_length=100, unique=True, db_index=True)
    session_id = models.CharField(max_length=100, db_index=True)
    turn_id = models.CharField(max_length=100, db_index=True)
    decision_id = models.CharField(max_length=100, db_index=True, blank=True, default="")
    message_id = models.CharField(max_length=100, db_index=True, blank=True, default="")

    # LangSmith trace IDs
    langsmith_run_id = models.CharField(max_length=100, blank=True, default="")
    langsmith_span_id = models.CharField(max_length=100, blank=True, default="")

    # Tool call details
    tool_name = models.CharField(max_length=100, db_index=True)
    tool_input = models.JSONField(help_text="Validated tool arguments")
    tool_output = models.JSONField(null=True, blank=True, help_text="Tool result")

    # Timing
    start_timestamp = models.DateTimeField()
    end_timestamp = models.DateTimeField(null=True, blank=True)
    latency_ms = models.IntegerField(null=True, blank=True)

    # Status
    success = models.BooleanField(default=True)
    error_message = models.TextField(blank=True, default="")
    error_type = models.CharField(max_length=100, blank=True, default="")

    # Flow context
    flow = models.CharField(max_length=20, blank=True, default="")

    class Meta:
        ordering = ["start_timestamp"]
        indexes = [
            models.Index(fields=["session_id", "start_timestamp"]),
            models.Index(fields=["tool_name", "start_timestamp"]),
            models.Index(fields=["turn_id"]),
        ]

    def __str__(self):
        status = "✓" if self.success else "✗"
        return f"{status} {self.tool_name} ({self.latency_ms}ms)"

    @classmethod
    def generate_event_id(cls):
        """Generate a unique event ID."""
        return f"evt_{uuid.uuid4().hex[:16]}"


class BraintrustLog(models.Model):
    """
    Stores Braintrust run logs for analytics and evals.

    Captures the full context of each turn for:
    - Offline evaluation
    - Dataset creation
    - Regression testing
    """

    # Identifiers
    log_id = models.CharField(max_length=100, unique=True, db_index=True)
    session_id = models.CharField(max_length=100, db_index=True)
    turn_id = models.CharField(max_length=100, db_index=True)
    decision_id = models.CharField(max_length=100, db_index=True, blank=True, default="")

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)

    # Input context
    user_message = models.TextField()
    conversation_summary = models.TextField(blank=True, default="")
    flow = models.CharField(max_length=20, blank=True, default="")

    # Prompt info
    core_prompt_id = models.CharField(max_length=100, blank=True, default="")
    core_prompt_version = models.CharField(max_length=50, blank=True, default="")
    flow_prompt_id = models.CharField(max_length=100, blank=True, default="")
    flow_prompt_version = models.CharField(max_length=50, blank=True, default="")

    # Tool info
    tools_available = models.JSONField(default=list)
    tools_used = models.JSONField(default=list)

    # Output
    assistant_response = models.TextField(blank=True, default="")
    was_refused = models.BooleanField(default=False)
    refusal_reason_codes = models.JSONField(default=list)

    # Metrics
    latency_ms = models.IntegerField(null=True, blank=True)
    llm_calls = models.IntegerField(null=True, blank=True)
    tool_calls_count = models.IntegerField(null=True, blank=True)
    input_tokens = models.IntegerField(null=True, blank=True)
    output_tokens = models.IntegerField(null=True, blank=True)
    estimated_cost_usd = models.FloatField(null=True, blank=True)

    # Tags for filtering
    environment = models.CharField(max_length=20, default="dev")
    app_version = models.CharField(max_length=50, blank=True, default="")

    # Raw data for Braintrust
    braintrust_input = models.JSONField(
        null=True, blank=True, help_text="Full input payload for Braintrust"
    )
    braintrust_output = models.JSONField(
        null=True, blank=True, help_text="Full output payload for Braintrust"
    )
    braintrust_metadata = models.JSONField(
        null=True, blank=True, help_text="Additional metadata for Braintrust"
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["session_id", "created_at"]),
            models.Index(fields=["flow", "created_at"]),
            models.Index(fields=["environment", "created_at"]),
        ]

    def __str__(self):
        return f"Log {self.log_id}: {self.flow} turn"

    @classmethod
    def generate_log_id(cls):
        """Generate a unique log ID."""
        return f"log_{uuid.uuid4().hex[:16]}"
