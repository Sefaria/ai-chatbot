"""Websocket routing for chat."""

from django.urls import path

from .V2.consumers import V2ChatConsumer

websocket_urlpatterns = [
    path("ws/v2/chat", V2ChatConsumer.as_asgi()),
    path("api/ws/v2/chat", V2ChatConsumer.as_asgi()),
]
