"""
Chat API URL configuration.
"""

from django.urls import path

from . import views
from .V2 import views as v2_views
from .V2.anthropic_views import chat_anthropic_v2
from .V2.local_views import local_guardrail, local_identity, local_prompt, local_route

urlpatterns = [
    # Versioned chat endpoints
    path("v2/chat/stream", v2_views.chat_stream_v2, name="chat_stream_v2"),
    path("chat/stream", v2_views.chat_stream_v2, name="chat_stream_v2"),
    path("v2/chat/recover", v2_views.chat_recover_v2, name="chat_recover_v2"),
    path("v2/chat/cancel", v2_views.chat_cancel_v2, name="chat_cancel_v2"),
    path("v2/chat/client-event", v2_views.chat_client_event_v2, name="chat_client_event_v2"),
    path("v2/chat/anthropic", chat_anthropic_v2, name="chat_anthropic_v2"),
    path("v2/chat/feedback", v2_views.chat_feedback_v2, name="chat_feedback_v2"),
    # Local agent runner
    path("v2/local/identity", local_identity, name="local_identity"),
    path("v2/local/prompt", local_prompt, name="local_prompt"),
    path("v2/local/guardrail", local_guardrail, name="local_guardrail"),
    path("v2/local/route", local_route, name="local_route"),
    path("v2/prompts/defaults", v2_views.prompt_defaults, name="prompt_defaults_v2"),
    # Shared endpoints
    path("history", views.history, name="history"),
    # Admin/management endpoints
    path("admin/reload-prompts", views.reload_prompts, name="reload_prompts"),
    path("health", views.health, name="health"),
]
