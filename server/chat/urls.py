"""
Chat API URL configuration.
"""

from django.urls import path
from django.views.decorators.http import require_GET

from . import views
from .V2 import views as v2_views
from .V2.anthropic_views import chat_anthropic_v2

urlpatterns = [
    # Versioned chat endpoints
    path("v2/chat/stream", v2_views.chat_stream_v2, name="chat_stream_v2"),
    path("chat/stream", v2_views.chat_stream_v2, name="chat_stream_v2"),
    path("v2/chat/anthropic", chat_anthropic_v2, name="chat_anthropic_v2"),
    path("v2/chat/feedback", v2_views.chat_feedback_v2, name="chat_feedback_v2"),
    path("v2/prompts/defaults", v2_views.prompt_defaults, name="prompt_defaults_v2"),
    # Shared endpoints
    path("history", views.history, name="history"),
    # Admin/management endpoints
    path("admin/reload-prompts", views.reload_prompts, name="reload_prompts"),
    path("health", views.health, name="health"),
    path("metrics", require_GET(views.metrics), name="metrics"),
]
