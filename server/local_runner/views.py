"""Runner-only endpoints."""

from __future__ import annotations

from django.http import JsonResponse

from . import DEFAULT_PORT  # noqa: F401  (kept for parity with __main__ defaults)


def health(request):
    """Liveness probe the widget uses to decide whether local mode is available.

    Unauthenticated on purpose: it answers "is a runner here", nothing more, and
    the widget needs an answer before it has presented a token.
    """
    return JsonResponse({"status": "ok", "mode": "local"})
