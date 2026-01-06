"""
URL configuration for chatbot_server project.
"""

from django.urls import path, include

urlpatterns = [
    path('api/', include('chat.urls')),
]

