"""Which Sefaria a request talks to: its Origin's Sefaria when allowed, else the default."""

import threading

import pytest
from django.http import HttpResponse, StreamingHttpResponse
from django.test import RequestFactory

from chat import sefaria_base_url
from chat.sefaria_base_url import SefariaBaseUrlMiddleware, allowed_sefaria_base_url
from chat.V2.agent.sefaria_client import SefariaClient, _get_default_sefaria_base_url

DEFAULT = "https://www.sefaria.org"
CAULDRON = "https://la-anon.cauldron.sefaria.org"


@pytest.fixture(autouse=True)
def default_sefaria(monkeypatch):
    monkeypatch.setenv("SEFARIA_API_BASE_URL", DEFAULT)
    token = sefaria_base_url._request_sefaria_base_url.set(None)
    yield
    sefaria_base_url._request_sefaria_base_url.reset(token)


@pytest.fixture
def allow(monkeypatch):
    def _allow(hosts: str):
        monkeypatch.setenv("CHATBOT_ALLOWED_SEFARIA_HOSTS", hosts)

    return _allow


def request_from(origin=None, view=None):
    """Run the middleware for a request with this Origin. Returns which Sefaria the
    view saw, and the response."""
    headers = {"HTTP_ORIGIN": origin} if origin else {}
    request = RequestFactory().post("/api/v2/chat/stream", **headers)
    seen = []

    def default_view(request):
        seen.append(_get_default_sefaria_base_url())
        return HttpResponse("ok")

    response = SefariaBaseUrlMiddleware(view or default_view)(request)
    return seen, response


@pytest.mark.parametrize(
    "origin, expected",
    [
        ("https://www.sefaria.org.il", "https://www.sefaria.org.il"),
        (CAULDRON, CAULDRON),
        (CAULDRON + "/", CAULDRON),
        ("http://localhost:8000", "http://localhost:8000"),
    ],
)
def test_an_allowed_origin_is_used(allow, origin, expected):
    allow("www.sefaria.org,www.sefaria.org.il,*.cauldron.sefaria.org,localhost")

    assert allowed_sefaria_base_url(origin) == expected


@pytest.mark.parametrize(
    "origin",
    [
        pytest.param("https://evil.example.com", id="other-site"),
        pytest.param("https://cauldron.sefaria.org", id="wildcard-needs-a-subdomain"),
        pytest.param("https://x.cauldron.sefaria.org.evil.com", id="lookalike"),
        pytest.param("ftp://www.sefaria.org.il", id="not-http"),
        pytest.param("https://www.sefaria.org.il/some/path", id="has-a-path"),
        pytest.param("https://user:pw@www.sefaria.org.il", id="has-credentials"),
        pytest.param("null", id="opaque-origin"),
        pytest.param("", id="empty"),
        pytest.param(None, id="none"),
    ],
)
def test_anything_else_is_not_used(allow, origin):
    allow("www.sefaria.org.il,*.cauldron.sefaria.org")

    assert allowed_sefaria_base_url(origin) is None


def test_with_no_allowed_hosts_nothing_is_used(monkeypatch):
    monkeypatch.delenv("CHATBOT_ALLOWED_SEFARIA_HOSTS", raising=False)

    assert allowed_sefaria_base_url("https://www.sefaria.org.il") is None


def test_hosts_match_case_insensitively(allow):
    allow("*.Cauldron.Sefaria.org")

    assert allowed_sefaria_base_url("https://LA-Anon.cauldron.sefaria.org") == CAULDRON


def test_a_request_from_an_allowed_origin_talks_to_that_sefaria(allow):
    allow("*.cauldron.sefaria.org")
    clients = []

    def view(request):
        clients.append(SefariaClient())
        return HttpResponse("ok")

    request_from(CAULDRON, view)

    assert clients[0].base_url == CAULDRON


def test_any_other_request_talks_to_the_default_sefaria(allow):
    allow("*.cauldron.sefaria.org")

    seen, _ = request_from("https://evil.example.com")

    assert seen == [DEFAULT]


def test_the_requests_sefaria_is_cleared_when_the_request_is_done(allow):
    allow("*.cauldron.sefaria.org")

    seen, _ = request_from(CAULDRON)

    assert seen == [CAULDRON]
    assert _get_default_sefaria_base_url() == DEFAULT


def test_a_streamed_answer_keeps_the_requests_sefaria_until_the_stream_ends(allow):
    allow("*.cauldron.sefaria.org")
    seen_while_streaming = []

    def stream():
        seen_while_streaming.append(_get_default_sefaria_base_url())
        yield b"answer"

    _, response = request_from(CAULDRON, lambda request: StreamingHttpResponse(stream()))

    # The view has returned, but the answer hasn't been produced yet.
    assert _get_default_sefaria_base_url() == CAULDRON
    b"".join(response.streaming_content)

    assert seen_while_streaming == [CAULDRON]
    assert _get_default_sefaria_base_url() == DEFAULT


@pytest.mark.django_db  # response.close() sends request_finished, which closes DB connections
def test_an_abandoned_stream_still_clears_the_requests_sefaria(allow):
    allow("*.cauldron.sefaria.org")

    def stream():
        yield b"first"
        yield b"second"

    _, response = request_from(CAULDRON, lambda request: StreamingHttpResponse(stream()))
    content = iter(response.streaming_content)
    next(content)
    response.close()  # the client went away; the server closes the response

    assert _get_default_sefaria_base_url() == DEFAULT


def test_an_error_in_the_view_clears_the_requests_sefaria(allow):
    allow("*.cauldron.sefaria.org")

    def failing_view(request):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        request_from(CAULDRON, failing_view)

    assert _get_default_sefaria_base_url() == DEFAULT


def test_the_middleware_is_registered():
    from django.conf import settings

    assert "chat.sefaria_base_url.SefariaBaseUrlMiddleware" in settings.MIDDLEWARE


def test_requests_at_the_same_time_each_see_only_their_own_sefaria(allow):
    # Each request is held inside its view until both are there, so they really overlap.
    allow("*.cauldron.sefaria.org")
    both_inside = threading.Barrier(2, timeout=5)
    seen = {}

    def run(origin):
        def view(request):
            both_inside.wait()
            seen[origin] = _get_default_sefaria_base_url()
            return HttpResponse("ok")

        request_from(origin, view)

    threads = [
        threading.Thread(target=run, args=(origin,))
        for origin in (CAULDRON, "https://other.cauldron.sefaria.org")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert seen == {
        CAULDRON: CAULDRON,
        "https://other.cauldron.sefaria.org": "https://other.cauldron.sefaria.org",
    }


def test_the_appetizer_never_takes_a_requests_sefaria(allow, monkeypatch):
    # The appetizer is a long-lived singleton: if it were created while a request's
    # Sefaria is set, it must still talk to the deployment's Sefaria.
    from chat.V2.appetizer import appetizer_service

    allow("*.cauldron.sefaria.org")
    monkeypatch.setattr(appetizer_service, "get_anthropic_client", lambda: object())
    created = []

    def view(request):
        created.append(appetizer_service.AppetizerService())
        return HttpResponse("ok")

    request_from(CAULDRON, view)

    assert created[0].sefaria_client.base_url == DEFAULT
