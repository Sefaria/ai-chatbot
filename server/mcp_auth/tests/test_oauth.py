"""The /mcp/user authorization server, driven end to end through Django's test client.

Sefaria's side is simulated: where a browser would visit sefaria.org/api/mcp/authorize and
be auto-POSTed back, the test mints the same ``mcp-login`` token and POSTs it itself.
"""

from __future__ import annotations

import base64
import hashlib
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from django.test import Client, override_settings
from django.utils import timezone

from mcp_auth import access_tokens, sefaria_tokens
from mcp_auth.models import AuthorizationRequest, Grant

ISSUER = "https://chat.test"
RESOURCE = f"{ISSUER}/mcp/user"
TOKEN_SECRET = "test-mcp-token-secret"
SIGNING_KEY = "test-signing-key-" + "x" * 32
SEFARIA_AUTHORIZE = "https://sefaria.test/api/mcp/authorize"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "v" * 64
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
)
USER_ID = 4242

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def mcp_settings():
    with override_settings(
        MCP_TOKEN_SECRET=TOKEN_SECRET,
        MCP_OAUTH_SIGNING_KEY=SIGNING_KEY,
        MCP_OAUTH_ISSUER=ISSUER,
        MCP_USER_RESOURCE_URL=RESOURCE,
        SEFARIA_MCP_AUTHORIZE_URL=SEFARIA_AUTHORIZE,
    ):
        yield


# ---------------------------------------------------------------------------
# Helpers that play the MCP client, the browser and Sefaria
# ---------------------------------------------------------------------------


def register(client: Client, redirect_uris=None, **extra):
    return client.post(
        "/oauth/register",
        {
            "client_name": "Claude",
            "redirect_uris": [REDIRECT] if redirect_uris is None else redirect_uris,
            **extra,
        },
        content_type="application/json",
    )


def client_id(client: Client, **kwargs) -> str:
    response = register(client, **kwargs)
    assert response.status_code == 201, response.content
    return response.json()["client_id"]


def authorize(browser: Client, cid: str, **overrides):
    params = {
        "response_type": "code",
        "client_id": cid,
        "redirect_uri": REDIRECT,
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        "state": "client-state",
        "scope": "sheets",
        "resource": RESOURCE,
        **overrides,
    }
    return browser.get("/oauth/authorize", {k: v for k, v in params.items() if v is not None})


def sefaria_state(response) -> str:
    """The state /oauth/authorize sent to Sefaria."""
    location = response["Location"]
    assert location.startswith(SEFARIA_AUTHORIZE + "?")
    query = parse_qs(urlsplit(location).query)
    assert query["redirect_uri"] == [f"{ISSUER}/oauth/sefaria/callback"]
    return query["state"][0]


def sefaria_posts_back(
    browser: Client, state: str, user_id: int = USER_ID, token: str | None = None
):
    token = token or sefaria_tokens.mint(
        user_id, TOKEN_SECRET, aud=sefaria_tokens.LOGIN_AUDIENCE, ttl=timedelta(minutes=5)
    )
    return browser.post("/oauth/sefaria/callback", {"state": state, "token": token})


def consent_form(browser: Client, state: str) -> dict:
    page = browser.get("/oauth/consent", {"request": state})
    assert page.status_code == 200
    assert page["X-Frame-Options"] == "DENY"
    html = page.content.decode()
    return {
        "request": re.search(r'name="request" value="([^"]+)"', html).group(1),
        "consent_token": re.search(r'name="consent_token" value="([^"]+)"', html).group(1),
    }


def approve(browser: Client, state: str, decision: str = "allow"):
    return browser.post("/oauth/consent", {**consent_form(browser, state), "decision": decision})


def code_from(response) -> str:
    assert response.status_code == 302
    location = urlsplit(response["Location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == REDIRECT
    query = parse_qs(location.query)
    assert query["state"] == ["client-state"]
    assert query["iss"] == [ISSUER]
    return query["code"][0]


def exchange(client: Client, cid: str, code: str, **overrides):
    return client.post(
        "/oauth/token",
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": cid,
            "redirect_uri": REDIRECT,
            "code_verifier": VERIFIER,
            **overrides,
        },
    )


def full_flow(cid: str | None = None):
    """Run the whole flow; returns (client_id, token response JSON)."""
    api = Client()
    cid = cid or client_id(api)
    browser = Client()
    state = sefaria_state(authorize(browser, cid))
    assert sefaria_posts_back(browser, state).status_code == 302
    code = code_from(approve(browser, state))
    response = exchange(api, cid, code)
    assert response.status_code == 200, response.content
    return cid, response.json()


def refresh(client: Client, cid: str, refresh_token: str):
    return client.post(
        "/oauth/token",
        {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": cid},
    )


# ---------------------------------------------------------------------------
# Metadata and registration
# ---------------------------------------------------------------------------


def test_metadata_describes_the_server():
    body = Client().get("/.well-known/oauth-authorization-server").json()
    assert body["issuer"] == ISSUER
    assert body["authorization_endpoint"] == f"{ISSUER}/oauth/authorize"
    assert body["token_endpoint"] == f"{ISSUER}/oauth/token"
    assert body["registration_endpoint"] == f"{ISSUER}/oauth/register"
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert body["token_endpoint_auth_methods_supported"] == ["none"]


@pytest.mark.parametrize(
    "uris",
    [
        [REDIRECT],
        ["http://localhost:6274/oauth/callback"],
        ["http://127.0.0.1/callback"],
    ],
)
def test_register_accepts_https_and_loopback(uris):
    assert register(Client(), uris).status_code == 201


@pytest.mark.parametrize(
    "uris",
    [
        ["http://claude.ai/callback"],
        ["javascript:alert(1)"],
        ["https://claude.ai/cb#frag"],
        ["https://user@claude.ai/cb"],
        ["not a url"],
        [],
        "https://claude.ai/cb",
    ],
)
def test_register_rejects_unsafe_redirects(uris):
    response = register(Client(), uris)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_redirect_uri"


def test_register_rejects_confidential_clients():
    response = register(Client(), token_endpoint_auth_method="client_secret_basic")
    assert response.status_code == 400


def test_endpoints_are_off_without_secrets():
    with override_settings(MCP_TOKEN_SECRET=None):
        assert register(Client()).status_code == 503
        assert Client().post("/oauth/token", {"grant_type": "refresh_token"}).status_code == 503


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_full_flow_issues_tokens_for_the_signed_in_user():
    cid, body = full_flow()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == access_tokens.ACCESS_TOKEN_TTL_SECONDS
    assert body["scope"] == "sheets"
    claims = jwt.decode(body["access_token"], SIGNING_KEY, algorithms=["HS256"], audience=RESOURCE)
    assert claims["iss"] == ISSUER
    assert claims["sefaria_user_id"] == USER_ID
    assert claims["client_id"] == cid
    assert Grant.objects.get(pk=claims["sub"]).sefaria_user_id == USER_ID
    # Secrets are stored hashed only.
    grant = Grant.objects.get(pk=claims["sub"])
    assert body["refresh_token"] not in grant.refresh_token_hash


def test_unknown_client_or_redirect_gets_an_error_page_not_a_redirect():
    cid = client_id(Client())
    assert authorize(Client(), "nope").status_code == 400
    response = authorize(Client(), cid, redirect_uri="https://evil.example/cb")
    assert response.status_code == 400
    assert "Location" not in response


def test_loopback_redirect_may_change_port():
    cid = client_id(Client(), redirect_uris=["http://127.0.0.1:1111/cb"])
    response = authorize(Client(), cid, redirect_uri="http://127.0.0.1:2222/cb")
    assert response["Location"].startswith(SEFARIA_AUTHORIZE)


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"code_challenge_method": "plain"}, "invalid_request"),
        ({"code_challenge": None}, "invalid_request"),
        ({"response_type": "token"}, "unsupported_response_type"),
        ({"scope": "sheets admin"}, "invalid_scope"),
        ({"resource": "https://chat.test/mcp"}, "invalid_target"),
    ],
)
def test_bad_authorize_requests_are_sent_back_to_the_client(overrides, error):
    cid = client_id(Client())
    response = authorize(Client(), cid, **overrides)
    query = parse_qs(urlsplit(response["Location"]).query)
    assert response["Location"].startswith(REDIRECT)
    assert query["error"] == [error]
    assert query["state"] == ["client-state"]


def test_callback_from_another_browser_is_refused():
    """Login CSRF: an attacker's flow completed in a victim's browser grants nothing."""
    cid = client_id(Client())
    attacker = Client()
    state = sefaria_state(authorize(attacker, cid))

    victim = Client()
    assert sefaria_posts_back(victim, state).status_code == 403
    assert AuthorizationRequest.objects.get(pk=state).sefaria_user_id is None


@pytest.mark.parametrize(
    "token",
    [
        "garbage",
        sefaria_tokens.mint(USER_ID, TOKEN_SECRET, aud=sefaria_tokens.API_AUDIENCE),
        sefaria_tokens.mint(USER_ID, "other-secret", aud=sefaria_tokens.LOGIN_AUDIENCE),
        sefaria_tokens.mint(
            USER_ID, TOKEN_SECRET, aud=sefaria_tokens.LOGIN_AUDIENCE, ttl=timedelta(seconds=-1)
        ),
    ],
)
def test_callback_rejects_bad_login_tokens(token):
    cid = client_id(Client())
    browser = Client()
    state = sefaria_state(authorize(browser, cid))
    assert sefaria_posts_back(browser, state, token=token).status_code == 400


def test_callback_cannot_be_replayed():
    cid = client_id(Client())
    browser = Client()
    state = sefaria_state(authorize(browser, cid))
    assert sefaria_posts_back(browser, state).status_code == 302
    assert sefaria_posts_back(browser, state, user_id=999).status_code == 400
    assert AuthorizationRequest.objects.get(pk=state).sefaria_user_id == USER_ID


def test_consent_requires_the_form_token():
    cid = client_id(Client())
    browser = Client()
    state = sefaria_state(authorize(browser, cid))
    sefaria_posts_back(browser, state)
    response = browser.post("/oauth/consent", {"request": state, "decision": "allow"})
    assert response.status_code == 403


def test_consent_from_another_browser_is_refused():
    cid = client_id(Client())
    browser = Client()
    state = sefaria_state(authorize(browser, cid))
    sefaria_posts_back(browser, state)
    assert Client().get("/oauth/consent", {"request": state}).status_code == 403


def test_declining_sends_access_denied():
    cid = client_id(Client())
    browser = Client()
    state = sefaria_state(authorize(browser, cid))
    sefaria_posts_back(browser, state)
    response = approve(browser, state, decision="deny")
    query = parse_qs(urlsplit(response["Location"]).query)
    assert query["error"] == ["access_denied"]
    assert "code" not in query


def test_expired_flow_is_refused():
    cid = client_id(Client())
    browser = Client()
    state = sefaria_state(authorize(browser, cid))
    AuthorizationRequest.objects.filter(pk=state).update(
        created_at=timezone.now() - timedelta(minutes=11)
    )
    assert sefaria_posts_back(browser, state).status_code == 400


# ---------------------------------------------------------------------------
# Token endpoint
# ---------------------------------------------------------------------------


def _code(cid: str) -> str:
    browser = Client()
    state = sefaria_state(authorize(browser, cid))
    sefaria_posts_back(browser, state)
    return code_from(approve(browser, state))


@pytest.mark.parametrize(
    "overrides",
    [
        {"code_verifier": "w" * 64},
        {"code_verifier": ""},
        {"redirect_uri": "https://claude.ai/other"},
        {"client_id": "someone-else"},
    ],
)
def test_code_exchange_checks_pkce_redirect_and_client(overrides):
    api = Client()
    cid = client_id(api)
    response = exchange(api, cid, _code(cid), **overrides)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


def test_code_reuse_revokes_the_grant_it_issued():
    api = Client()
    cid = client_id(api)
    code = _code(cid)
    first = exchange(api, cid, code).json()
    assert exchange(api, cid, code).json()["error"] == "invalid_grant"
    assert refresh(api, cid, first["refresh_token"]).json()["error"] == "invalid_grant"


def test_expired_code_is_refused():
    api = Client()
    cid = client_id(api)
    code = _code(cid)
    AuthorizationRequest.objects.update(code_expires_at=timezone.now() - timedelta(seconds=1))
    assert exchange(api, cid, code).json()["error"] == "invalid_grant"


def test_refresh_rotates_and_detects_reuse():
    cid, first = full_flow()
    api = Client()
    second = refresh(api, cid, first["refresh_token"])
    assert second.status_code == 200
    second = second.json()
    assert second["refresh_token"] != first["refresh_token"]

    # Replaying the rotated token looks like theft: the whole grant is revoked.
    assert refresh(api, cid, first["refresh_token"]).json()["error"] == "invalid_grant"
    assert refresh(api, cid, second["refresh_token"]).json()["error"] == "invalid_grant"


def test_replaying_any_older_token_revokes_the_family():
    """RFC 9700 4.14.2: rotation history is kept for the grant's life, not just one step."""
    cid, r0 = full_flow()
    api = Client()
    r1 = refresh(api, cid, r0["refresh_token"]).json()
    r2 = refresh(api, cid, r1["refresh_token"]).json()

    assert refresh(api, cid, r0["refresh_token"]).json()["error"] == "invalid_grant"
    assert refresh(api, cid, r2["refresh_token"]).json()["error"] == "invalid_grant"


def test_revoking_a_rotated_token_revokes_its_descendants():
    cid, r0 = full_flow()
    api = Client()
    r1 = refresh(api, cid, r0["refresh_token"]).json()
    assert api.post("/oauth/revoke", {"token": r0["refresh_token"]}).status_code == 200
    assert refresh(api, cid, r1["refresh_token"]).json()["error"] == "invalid_grant"


def test_refresh_requires_the_same_client():
    cid, first = full_flow()
    other = client_id(Client())
    assert refresh(Client(), other, first["refresh_token"]).json()["error"] == "invalid_grant"


def test_refresh_stops_at_idle_and_absolute_expiry():
    cid, first = full_flow()
    Grant.objects.update(refresh_expires_at=timezone.now() - timedelta(seconds=1))
    assert refresh(Client(), cid, first["refresh_token"]).json()["error"] == "invalid_grant"

    cid, second = full_flow()
    Grant.objects.filter(client__client_id=cid).update(
        absolute_expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert refresh(Client(), cid, second["refresh_token"]).json()["error"] == "invalid_grant"


def test_revoke_ends_the_grant():
    cid, body = full_flow()
    assert Client().post("/oauth/revoke", {"token": body["refresh_token"]}).status_code == 200
    assert refresh(Client(), cid, body["refresh_token"]).json()["error"] == "invalid_grant"
    # Unknown tokens are accepted silently, as RFC 7009 requires.
    assert Client().post("/oauth/revoke", {"token": "nope"}).status_code == 200


def test_unsupported_grant_type():
    response = Client().post("/oauth/token", {"grant_type": "password"})
    assert response.json()["error"] == "unsupported_grant_type"


# ---------------------------------------------------------------------------
# Bounds on anonymous writes
# ---------------------------------------------------------------------------


def test_registration_rejects_oversized_metadata():
    assert register(Client(), client_name="x" * 9000).status_code == 400
    long_uri = "https://claude.ai/" + "a" * 2100
    assert register(Client(), redirect_uris=[long_uri]).json()["error"] == "invalid_redirect_uri"


def test_registration_has_a_global_hourly_ceiling(monkeypatch):
    from mcp_auth import views

    monkeypatch.setattr(views, "MAX_NEW_CLIENTS_PER_HOUR", 2)
    assert register(Client()).status_code == 201
    assert register(Client()).status_code == 201
    busy = register(Client())
    assert busy.status_code == 429
    assert busy["Retry-After"] == "60"


def test_registration_clears_clients_that_never_signed_in():
    from mcp_auth.models import OAuthClient

    stale = client_id(Client())
    cid, _ = full_flow()  # completed a sign-in, so it has a grant
    OAuthClient.objects.update(created_at=timezone.now() - timedelta(days=2))

    client_id(Client())

    ids = set(OAuthClient.objects.values_list("client_id", flat=True))
    assert stale not in ids
    assert cid in ids


def test_pending_authorizations_have_a_global_ceiling(monkeypatch):
    from mcp_auth import views

    monkeypatch.setattr(views, "MAX_PENDING_AUTHORIZATIONS", 1)
    cid = client_id(Client())
    assert authorize(Client(), cid).status_code == 302
    assert authorize(Client(), cid).status_code == 429
