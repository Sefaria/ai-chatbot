"""
Chat API URL configuration.
"""

from django.urls import path
from . import views

urlpatterns = [
    # Core chat endpoints
    path('chat', views.chat, name='chat'),
    path('chat/stream', views.chat_stream, name='chat_stream'),
    path('history', views.history, name='history'),
    
    # Admin/management endpoints
    path('admin/reload-prompts', views.reload_prompts, name='reload_prompts'),
    path('health', views.health, name='health'),
]
