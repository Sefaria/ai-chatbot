"""The personalization settings endpoint (skeleton: Task 2 fills it)."""

from __future__ import annotations

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["POST"])
def personalization_settings(request):
    return Response({"error": "not_implemented"}, status=status.HTTP_501_NOT_IMPLEMENTED)
