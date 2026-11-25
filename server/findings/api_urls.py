from django.urls import path

from . import api

urlpatterns = [
    path("findings", api.findings_collection, name="findings_api"),
    path("findings/ref/<path:ref>", api.findings_for_ref, name="findings_api_ref"),
    path("findings/<uuid:finding_id>", api.finding_detail, name="findings_api_detail"),
    path("methods", api.methods, name="findings_api_methods"),
]
