"""OAuth 2.1 authorization server endpoints for /mcp/user (authorization code + PKCE).

The flow, end to end, is in docs/plans/2026-10-18-mcp-consolidation.md (Phase 2). In short:
/oauth/authorize binds a flow to the browser and sends it to Sefaria to sign in; Sefaria
auto-POSTs an ``mcp-login`` token to /oauth/sefaria/callback; the user approves on
/oauth/consent; the client redeems the code at /oauth/token for a 15-minute access JWT and
a rotating refresh token.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import access_tokens, sefaria_tokens
from .models import (
    AUTHORIZATION_CODE_TTL,
    AUTHORIZATION_REQUEST_TTL,
    GRANT_ABSOLUTE_TTL,
    REFRESH_TOKEN_IDLE_TTL,
    AuthorizationRequest,
    Grant,
    OAuthClient,
    hash_secret,
    new_secret,
    secret_matches,
)

SUPPORTED_SCOPES = {access_tokens.SCOPE}
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
PKCE_VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
MAX_REDIRECT_URIS = 10
BINDING_COOKIE_PREFIX = "mcp_oauth_"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def issuer() -> str:
    return settings.MCP_OAUTH_ISSUER.rstrip("/")


def resource_url() -> str:
    return settings.MCP_USER_RESOURCE_URL.rstrip("/")


def callback_url() -> str:
    return f"{issuer()}/oauth/sefaria/callback"


def _enabled() -> bool:
    return bool(settings.MCP_TOKEN_SECRET and settings.MCP_OAUTH_SIGNING_KEY)


def _disabled_response() -> JsonResponse:
    return JsonResponse({"error": "temporarily_unavailable"}, status=503)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def valid_redirect_uri(uri: str) -> bool:
    """https anywhere, or http on a loopback host for desktop clients (RFC 8252)."""
    try:
        parts = urlsplit(uri)
    except ValueError:
        return False
    if parts.fragment or not parts.hostname or parts.username or parts.password:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS


def redirect_uri_registered(client: OAuthClient, uri: str) -> bool:
    """Exact match, except that a loopback redirect may use any port (RFC 8252 §7.3)."""
    if uri in client.redirect_uris:
        return True

    def portless(value: str):
        try:
            parts = urlsplit(value)
        except ValueError:
            return None
        if parts.scheme != "http" or parts.hostname not in LOOPBACK_HOSTS:
            return None
        return (parts.hostname, parts.path, parts.query)

    candidate = portless(uri)
    return candidate is not None and any(portless(r) == candidate for r in client.redirect_uris)


def pkce_matches(verifier: str, challenge: str) -> bool:
    if not verifier or not PKCE_VERIFIER_RE.match(verifier):
        return False
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(expected, challenge)


def _redirect_with(uri: str, **params: str) -> HttpResponseRedirect:
    separator = "&" if urlsplit(uri).query else "?"
    params = {k: v for k, v in params.items() if v}
    return HttpResponseRedirect(f"{uri}{separator}{urlencode(params)}")


def _page(request: HttpRequest, template: str, context: dict, status: int = 200) -> HttpResponse:
    response = render(request, f"mcp_auth/{template}", context, status=status)
    response["Cache-Control"] = "no-store"
    response["X-Frame-Options"] = "DENY"
    response["Referrer-Policy"] = "no-referrer"
    return response


def _error_page(request: HttpRequest, message: str, status: int = 400) -> HttpResponse:
    return _page(request, "error.html", {"message": message}, status=status)


def _token_error(error: str, description: str = "", status: int = 400) -> JsonResponse:
    body = {"error": error}
    if description:
        body["error_description"] = description
    response = JsonResponse(body, status=status)
    response["Cache-Control"] = "no-store"
    return response


def _binding_cookie(request_id: str) -> str:
    return f"{BINDING_COOKIE_PREFIX}{request_id[:16]}"


def _browser_bound(request: HttpRequest, auth_request: AuthorizationRequest) -> bool:
    return secret_matches(
        request.COOKIES.get(_binding_cookie(auth_request.id)), auth_request.browser_binding_hash
    )


def _open_request(request_id: str) -> AuthorizationRequest | None:
    auth_request = (
        AuthorizationRequest.objects.select_related("client").filter(pk=request_id).first()
    )
    if auth_request is None or auth_request.expired or auth_request.code_hash:
        return None
    return auth_request


# ---------------------------------------------------------------------------
# Metadata and registration
# ---------------------------------------------------------------------------


@require_GET
def metadata(request: HttpRequest) -> JsonResponse:
    """RFC 8414 authorization server metadata."""
    base = issuer()
    return JsonResponse(
        {
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register",
            "revocation_endpoint": f"{base}/oauth/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "revocation_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": sorted(SUPPORTED_SCOPES),
            "authorization_response_iss_parameter_supported": True,
        }
    )


@require_POST
def register(request: HttpRequest) -> JsonResponse:
    """RFC 7591 dynamic client registration, for public clients only."""
    if not _enabled():
        return _disabled_response()
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return _token_error("invalid_client_metadata", "Body must be JSON.")
    if not isinstance(body, dict):
        return _token_error("invalid_client_metadata", "Body must be a JSON object.")

    redirect_uris = body.get("redirect_uris")
    if (
        not isinstance(redirect_uris, list)
        or not 1 <= len(redirect_uris) <= MAX_REDIRECT_URIS
        or not all(isinstance(uri, str) and valid_redirect_uri(uri) for uri in redirect_uris)
    ):
        return _token_error(
            "invalid_redirect_uri", "redirect_uris must be https, or http on a loopback host."
        )
    if body.get("token_endpoint_auth_method", "none") != "none":
        return _token_error(
            "invalid_client_metadata", "Only public clients are supported (auth method 'none')."
        )

    client = OAuthClient.objects.create(
        client_name=str(body.get("client_name") or "")[:200], redirect_uris=redirect_uris
    )
    return JsonResponse(
        {
            "client_id": client.client_id,
            "client_id_issued_at": int(client.created_at.timestamp()),
            "client_name": client.client_name,
            "redirect_uris": client.redirect_uris,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": access_tokens.SCOPE,
        },
        status=201,
    )


# ---------------------------------------------------------------------------
# Authorization: authorize -> Sefaria sign-in -> callback -> consent
# ---------------------------------------------------------------------------


@require_GET
def authorize(request: HttpRequest) -> HttpResponse:
    if not _enabled():
        return _error_page(request, "Sign-in is not available right now.", status=503)

    params = request.GET
    client = OAuthClient.objects.filter(client_id=params.get("client_id", "")).first()
    redirect_uri = params.get("redirect_uri", "")
    # Until the client and redirect are known good, errors cannot be sent back to it.
    if client is None:
        return _error_page(request, "Unknown application.")
    if not redirect_uri_registered(client, redirect_uri):
        return _error_page(request, "This application's redirect address is not registered.")

    state = params.get("state", "")

    def fail(error: str, description: str) -> HttpResponse:
        return _redirect_with(
            redirect_uri, error=error, error_description=description, state=state, iss=issuer()
        )

    if params.get("response_type") != "code":
        return fail("unsupported_response_type", "Only response_type=code is supported.")
    challenge = params.get("code_challenge", "")
    if params.get("code_challenge_method") != "S256" or not 43 <= len(challenge) <= 128:
        return fail("invalid_request", "PKCE with code_challenge_method=S256 is required.")
    scope = params.get("scope") or access_tokens.SCOPE
    if not set(scope.split()) <= SUPPORTED_SCOPES:
        return fail("invalid_scope", f"Supported scopes: {' '.join(sorted(SUPPORTED_SCOPES))}.")
    resource = params.get("resource")
    if resource and resource.rstrip("/") != resource_url():
        return fail("invalid_target", f"This server only issues tokens for {resource_url()}.")

    AuthorizationRequest.objects.filter(
        created_at__lt=timezone.now() - AUTHORIZATION_REQUEST_TTL - timedelta(days=1)
    ).delete()

    binding = new_secret()
    auth_request = AuthorizationRequest.objects.create(
        client=client,
        redirect_uri=redirect_uri,
        client_state=state,
        code_challenge=challenge,
        scope=scope,
        browser_binding_hash=hash_secret(binding),
    )
    sign_in = (
        f"{settings.SEFARIA_MCP_AUTHORIZE_URL}?"
        f"{urlencode({'state': auth_request.id, 'redirect_uri': callback_url()})}"
    )
    response = HttpResponseRedirect(sign_in)
    response.set_cookie(
        _binding_cookie(auth_request.id),
        binding,
        max_age=int(AUTHORIZATION_REQUEST_TTL.total_seconds()),
        path="/oauth/",
        secure=issuer().startswith("https://"),
        httponly=True,
        samesite="Lax",
    )
    return response


@require_POST
def sefaria_callback(request: HttpRequest) -> HttpResponse:
    """Sefaria's auto-POSTed sign-in result. Cross-site by design, so no CSRF token."""
    auth_request = _open_request(request.POST.get("state", ""))
    if auth_request is None or auth_request.sefaria_user_id is not None:
        return _error_page(request, "This sign-in link has expired. Start again from your app.")
    if not _browser_bound(request, auth_request):
        return _error_page(
            request, "Finish signing in from the same browser you started in.", status=403
        )

    user_id = sefaria_tokens.user_id_from(
        request.POST.get("token", ""),
        settings.MCP_TOKEN_SECRET,
        aud=sefaria_tokens.LOGIN_AUDIENCE,
    )
    if user_id is None:
        return _error_page(request, "Sefaria sign-in could not be verified. Start again.")

    auth_request.sefaria_user_id = user_id
    auth_request.save(update_fields=["sefaria_user_id"])
    return HttpResponseRedirect(f"/oauth/consent?{urlencode({'request': auth_request.id})}")


@require_http_methods(["GET", "POST"])
def consent(request: HttpRequest) -> HttpResponse:
    source = request.POST if request.method == "POST" else request.GET
    auth_request = _open_request(source.get("request", ""))
    if auth_request is None or auth_request.sefaria_user_id is None:
        return _error_page(request, "This sign-in link has expired. Start again from your app.")
    if not _browser_bound(request, auth_request):
        return _error_page(
            request, "Finish signing in from the same browser you started in.", status=403
        )

    if request.method == "GET":
        consent_token = new_secret()
        auth_request.consent_token_hash = hash_secret(consent_token)
        auth_request.save(update_fields=["consent_token_hash"])
        return _page(
            request,
            "consent.html",
            {
                "client_name": auth_request.client.client_name or "An application",
                "redirect_host": urlsplit(auth_request.redirect_uri).hostname,
                "request_id": auth_request.id,
                "consent_token": consent_token,
            },
        )

    if not secret_matches(source.get("consent_token"), auth_request.consent_token_hash):
        return _error_page(request, "This approval form has expired. Start again.", status=403)

    cookie = _binding_cookie(auth_request.id)
    response: HttpResponse
    if source.get("decision") != "allow":
        auth_request.delete()
        response = _redirect_with(
            auth_request.redirect_uri,
            error="access_denied",
            error_description="The user declined access.",
            state=auth_request.client_state,
            iss=issuer(),
        )
    else:
        code = new_secret()
        auth_request.code_hash = hash_secret(code)
        auth_request.code_expires_at = timezone.now() + AUTHORIZATION_CODE_TTL
        auth_request.consent_token_hash = ""
        auth_request.save(update_fields=["code_hash", "code_expires_at", "consent_token_hash"])
        response = _redirect_with(
            auth_request.redirect_uri, code=code, state=auth_request.client_state, iss=issuer()
        )
    response.delete_cookie(cookie, path="/oauth/")
    return response


# ---------------------------------------------------------------------------
# Token and revocation
# ---------------------------------------------------------------------------


def _token_response(grant: Grant, refresh_token: str) -> JsonResponse:
    access_token = access_tokens.issue(
        signing_key=settings.MCP_OAUTH_SIGNING_KEY,
        issuer=issuer(),
        audience=resource_url(),
        grant_id=str(grant.id),
        sefaria_user_id=grant.sefaria_user_id,
        client_id=grant.client.client_id,
        scope=grant.scope,
    )
    response = JsonResponse(
        {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": access_tokens.ACCESS_TOKEN_TTL_SECONDS,
            "refresh_token": refresh_token,
            "scope": grant.scope,
        }
    )
    response["Cache-Control"] = "no-store"
    return response


def _exchange_code(params) -> JsonResponse:
    code = params.get("code", "")
    with transaction.atomic():
        auth_request = (
            AuthorizationRequest.objects.select_for_update()
            .select_related("client")
            .filter(code_hash=hash_secret(code))
            .first()
            if code
            else None
        )
        if auth_request is None:
            return _token_error("invalid_grant", "Unknown or expired authorization code.")
        if auth_request.grant_id is not None:
            # A code redeemed twice may have been intercepted: revoke what it issued.
            Grant.objects.get(pk=auth_request.grant_id).revoke()
            return _token_error("invalid_grant", "Authorization code already used.")
        if timezone.now() > auth_request.code_expires_at:
            return _token_error("invalid_grant", "Authorization code expired.")
        if params.get("client_id") != auth_request.client.client_id:
            return _token_error("invalid_grant", "Code was issued to another client.")
        if params.get("redirect_uri") != auth_request.redirect_uri:
            return _token_error(
                "invalid_grant", "redirect_uri does not match the authorization request."
            )
        if not pkce_matches(params.get("code_verifier", ""), auth_request.code_challenge):
            return _token_error("invalid_grant", "PKCE verification failed.")

        refresh_token = new_secret()
        now = timezone.now()
        grant = Grant.objects.create(
            client=auth_request.client,
            sefaria_user_id=auth_request.sefaria_user_id,
            scope=auth_request.scope,
            refresh_token_hash=hash_secret(refresh_token),
            refresh_expires_at=now + REFRESH_TOKEN_IDLE_TTL,
            absolute_expires_at=now + GRANT_ABSOLUTE_TTL,
            last_used_at=now,
        )
        auth_request.grant = grant
        auth_request.save(update_fields=["grant"])
    return _token_response(grant, refresh_token)


def _refresh(params) -> JsonResponse:
    with transaction.atomic():
        grant, current = Grant.for_refresh_token(params.get("refresh_token", ""))
        if grant is None:
            return _token_error("invalid_grant", "Unknown refresh token.")
        if not current:
            # A rotated-out token came back: assume the family was stolen and end it.
            grant.revoke()
            return _token_error("invalid_grant", "Unknown refresh token.")
        grant = Grant.objects.select_for_update().select_related("client").get(pk=grant.pk)
        if grant.refresh_token_hash != hash_secret(params.get("refresh_token", "")):
            # Another request rotated this token while we waited for the lock: a replay.
            grant.revoke()
            return _token_error("invalid_grant", "Unknown refresh token.")
        if not grant.active:
            return _token_error("invalid_grant", "This sign-in has expired or was revoked.")
        if params.get("client_id") != grant.client.client_id:
            return _token_error("invalid_grant", "Refresh token was issued to another client.")
        refresh_token = grant.rotate_refresh_token()
        grant.save()
    return _token_response(grant, refresh_token)


@require_POST
def token(request: HttpRequest) -> JsonResponse:
    if not _enabled():
        return _disabled_response()
    grant_type = request.POST.get("grant_type")
    if grant_type == "authorization_code":
        return _exchange_code(request.POST)
    if grant_type == "refresh_token":
        return _refresh(request.POST)
    return _token_error("unsupported_grant_type")


@require_POST
def revoke(request: HttpRequest) -> HttpResponse:
    """RFC 7009. Revokes the grant behind a refresh token; always 200, as the RFC requires."""
    grant, _ = Grant.for_refresh_token(request.POST.get("token", ""))
    if grant is not None:
        grant.revoke()
    return HttpResponse(status=200)
