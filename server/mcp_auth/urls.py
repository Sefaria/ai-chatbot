from django.urls import path

from . import views

urlpatterns = [
    path(".well-known/oauth-authorization-server", views.metadata),
    path("oauth/register", views.register),
    path("oauth/authorize", views.authorize),
    path("oauth/sefaria/callback", views.sefaria_callback),
    path("oauth/consent", views.consent),
    path("oauth/token", views.token),
    path("oauth/revoke", views.revoke),
]
