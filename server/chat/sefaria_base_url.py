"""
Which Sefaria the current request talks to, set once per request.

The widget runs inside a Sefaria page and calls this server cross-origin, so the
browser sends the page's address in the `Origin` header. If its host is allowed
for this deployment (`CHATBOT_ALLOWED_SEFARIA_HOSTS`, comma-separated; `*.example.org`
matches any subdomain), that Sefaria is used for the whole request: the limit, the
tools and the answer link checks all get it through `_get_default_sefaria_base_url()`
in sefaria_client.py. Otherwise, and when the variable is unset, the request uses
`SEFARIA_API_BASE_URL`, as before.

Production allows only its own Sefaria; a development deployment can allow cauldrons,
so a preview follows the cauldron that loads it.
"""

import contextvars
import os
from urllib.parse import urlsplit

# Set by SefariaBaseUrlMiddleware for the duration of a request (None = the default)
# and cleared when the request is done. For a streamed answer that's when the stream
# ends, not when the view returns: the answer is produced after the view returns, and
# the agent thread gets a copy of this context then.
_request_sefaria_base_url: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_sefaria_base_url", default=None
)


def request_sefaria_base_url() -> str | None:
    """The current request's Sefaria, or None to use the default."""
    return _request_sefaria_base_url.get()


def _allowed_host_patterns() -> list[str]:
    raw = os.environ.get("CHATBOT_ALLOWED_SEFARIA_HOSTS", "")
    return [pattern.strip().lower() for pattern in raw.split(",") if pattern.strip()]


def _is_allowed_host(host: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern.startswith("*."):
            # A subdomain of the pattern's domain, never the domain itself.
            if host.endswith(pattern[1:]):
                return True
        elif host == pattern:
            return True
    return False


def allowed_sefaria_base_url(origin: str | None) -> str | None:
    """The Sefaria base URL for a request's Origin if its host is allowed, else None."""
    if not origin:
        return None
    try:
        parts = urlsplit(origin.strip())
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return None

    if parts.scheme not in ("http", "https") or not host:
        return None
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return None
    if parts.username or parts.password:
        return None
    if not _is_allowed_host(host, _allowed_host_patterns()):
        return None

    return f"{parts.scheme}://{host}" + (f":{port}" if port else "")


def _clear() -> None:
    # set(None), not reset(token): a stream may be consumed in a different context
    # than the one the value was set in, where reset() would raise.
    _request_sefaria_base_url.set(None)


def _clear_when_done(content):
    """Pass a streamed body through, clearing the request's Sefaria when it ends
    (finished, or abandoned because the client went away)."""
    try:
        yield from content
    finally:
        _clear()


class SefariaBaseUrlMiddleware:
    """Decide the request's Sefaria once, from its Origin header, and clear it when
    the request is done."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _request_sefaria_base_url.set(allowed_sefaria_base_url(request.headers.get("Origin")))
        try:
            response = self.get_response(request)
        except BaseException:
            _clear()
            raise
        if getattr(response, "streaming", False):
            response.streaming_content = _clear_when_done(response.streaming_content)
        else:
            _clear()
        return response
