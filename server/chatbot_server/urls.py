"""
URL configuration for chatbot_server project.
"""

from django.urls import include, path

from chat.V2.personalization.api import lab_page

urlpatterns = [
    path("lab/", lab_page, name="personalization_lab"),
    path("api/", include("chat.urls")),
    path("", include("mcp_auth.urls")),
]
