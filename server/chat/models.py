"""
Chat models for message persistence and logging.
"""

from django.db import models
import uuid


class ChatMessage(models.Model):
    """
    Stores all chat messages for analytics and debugging.
    """
    
    class Role(models.TextChoices):
        USER = 'user', 'User'
        ASSISTANT = 'assistant', 'Assistant'
    
    class Status(models.TextChoices):
        SUCCESS = 'success', 'Success'
        FAILED = 'failed', 'Failed'
    
    # Identifiers
    message_id = models.CharField(max_length=100, unique=True, db_index=True)
    session_id = models.CharField(max_length=100, db_index=True)
    user_id = models.CharField(max_length=100, db_index=True)
    
    # Message content
    role = models.CharField(max_length=10, choices=Role.choices)
    content = models.TextField()
    
    # Timestamps
    client_timestamp = models.DateTimeField(null=True, blank=True)
    server_timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    
    # For user messages: link to the assistant response
    response_message = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='request_message'
    )
    
    # Metadata
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.SUCCESS
    )
    latency_ms = models.IntegerField(null=True, blank=True)
    
    # Context from client
    page_url = models.URLField(max_length=2000, blank=True, default='')
    locale = models.CharField(max_length=10, blank=True, default='')
    client_version = models.CharField(max_length=20, blank=True, default='')
    
    # Agent metadata (for assistant messages)
    llm_calls = models.IntegerField(null=True, blank=True)
    tool_calls_count = models.IntegerField(null=True, blank=True)
    tool_calls_data = models.JSONField(null=True, blank=True)  # List of tool calls
    input_tokens = models.IntegerField(null=True, blank=True)
    output_tokens = models.IntegerField(null=True, blank=True)
    cache_creation_tokens = models.IntegerField(null=True, blank=True)
    cache_read_tokens = models.IntegerField(null=True, blank=True)
    model_name = models.CharField(max_length=100, blank=True, default='')
    
    class Meta:
        ordering = ['server_timestamp']
        indexes = [
            models.Index(fields=['session_id', 'server_timestamp']),
            models.Index(fields=['user_id', 'server_timestamp']),
        ]
    
    def __str__(self):
        return f"{self.role}: {self.content[:50]}..."
    
    @classmethod
    def generate_message_id(cls):
        """Generate a unique message ID."""
        return f"msg_{uuid.uuid4().hex[:16]}"


class ChatSession(models.Model):
    """
    Tracks chat sessions for analytics.
    """
    
    session_id = models.CharField(max_length=100, unique=True, db_index=True)
    user_id = models.CharField(max_length=100, db_index=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    last_activity = models.DateTimeField(auto_now=True)
    
    message_count = models.IntegerField(default=0)
    
    # Aggregate token usage for the session
    total_input_tokens = models.IntegerField(default=0)
    total_output_tokens = models.IntegerField(default=0)
    total_tool_calls = models.IntegerField(default=0)
    
    class Meta:
        ordering = ['-last_activity']
    
    def __str__(self):
        return f"Session {self.session_id} (user: {self.user_id})"
