from django.urls import path

from . import views

app_name = "findings"

urlpatterns = [
    path("", views.home, name="home"),
    path("methods/<slug:slug>/", views.method, name="method"),
    path("skills/<slug:name>/", views.skill, name="skill"),
    path("skills/<slug:name>/<path:path>", views.skill_file, name="skill_file"),
    path("ref/<path:ref>/", views.passage, name="passage"),
    path("<uuid:finding_id>/", views.detail, name="detail"),
]
