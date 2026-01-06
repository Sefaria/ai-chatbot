"""
Chat API URL configuration.
"""

from django.urls import path
from . import views

urlpatterns = [
    path('chat', views.chat, name='chat'),
    path('chat/stream', views.chat_stream, name='chat_stream'),
    path('history', views.history, name='history'),
    path('admin/reload-prompt', views.reload_prompt, name='reload_prompt'),
]

