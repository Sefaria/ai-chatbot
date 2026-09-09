"""Bearer enforcement for every route the daemon serves.

The daemon listens on loopback, and any page in the user's browser can reach
loopback. CORS does not help: it governs whether a caller may *read* a response,
not whether the request is delivered. So the runner token is the actual control,
and it has to cover every route — including ``/api/history``, which takes its
user from query parameters and never calls the auth service.

Enforcing it here rather than per-view means a route added later is protected by
default instead of by remembering.
"""

from __future__ import annotations

from django.http import JsonResponse

# /health answers "is a runner here" before the page holds a token.
# /pair is how a page gets one; it carries its own proof (the terminal code).
EXEMPT_PATHS = frozenset({"/health", "/pair"})

BEARER_PREFIX = "Bearer "


def _unauthorized(reason: str) -> JsonResponse:
    return JsonResponse({"error": "runner_unauthorized", "reason": reason}, status=401)


def _presented_token(request) -> str | None:
    header = request.headers.get("Authorization", "")
    if not header.startswith(BEARER_PREFIX):
        return None
    return header[len(BEARER_PREFIX) :].strip() or None


class RunnerAuthMiddleware:
    """Reject anything that does not carry this daemon's runner token."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path in EXEMPT_PATHS:
            return self.get_response(request)

        # Import here so the module stays importable without the data directory.
        from . import pairing

        if not pairing.token_matches(_presented_token(request)):
            return _unauthorized("missing or invalid runner token")

        return self.get_response(request)


class OriginAllowlistMiddleware:
    """Reject cross-origin requests from anywhere but sefaria.org.

    Defence in depth behind the runner token: CORS already stops a foreign page
    reading responses, but a stolen token should still not be usable from an
    arbitrary origin. Requests with no Origin (curl, the pairing flow) pass —
    those are not browser-driven.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings

        origin = request.headers.get("Origin")
        if origin and origin not in settings.CORS_ALLOWED_ORIGINS:
            return _unauthorized("origin not allowed")

        return self.get_response(request)
