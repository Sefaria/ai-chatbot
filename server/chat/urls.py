"""
Chat API URL configuration.
"""

from django.urls import path

from . import views

urlpatterns = [
    # Core chat endpoints
    path("v2/chat/stream", views.chat_stream_v2, name="chat_stream_v2"),
    path("v2/prompts/defaults", views.prompt_defaults, name="prompt_defaults"),
    path("history", views.history, name="history"),
    # Admin/management endpoints
    path("admin/reload-prompts", views.reload_prompts, name="reload_prompts"),
    path("health", views.health, name="health"),
]
