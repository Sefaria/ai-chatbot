"""
URL configuration for chatbot_server project.
"""

from django.urls import include, path

urlpatterns = [
    path("api/", include("chat.urls")),
]
