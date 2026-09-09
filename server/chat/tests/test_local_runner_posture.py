"""The local runner's settings and URL surface must stay safe to ship.

These assert the two rules the daemon depends on (see the module docstring in
``local_runner/settings.py``): no server secrets reach a user's machine, and the
daemon is reachable only from loopback and only by sefaria.org.

The settings module is imported as a plain module rather than activated — Django
settings are process-global and the suite runs under ``chatbot_server.test_settings``.
"""

from __future__ import annotations

from django.urls import Resolver404, resolve

from local_runner import settings as local_settings

URLCONF = "local_runner.urls"

SERVED = [
    "/api/v2/chat/stream",
    "/api/v2/chat/recover",
    "/api/v2/chat/client-event",
    "/api/v2/chat/feedback",
    "/api/v2/prompts/defaults",
    "/api/history",
    "/health",
]

# Server-side concerns that must not be reachable on a user's machine.
NOT_SERVED = [
    "/api/v2/chat/anthropic",
    "/api/admin/reload-prompts",
]


class TestNoServerSecrets:
    def test_user_token_secret_is_cleared(self):
        # Explicitly cleared, because importing the base settings runs load_dotenv
        # and would otherwise inherit a developer's own .env.
        assert local_settings.CHATBOT_USER_TOKEN_SECRET == ""

    def test_braintrust_logging_is_off(self):
        assert local_settings.BRAINTRUST_LOGGING_ENABLED is False

    def test_sentry_is_off(self):
        assert local_settings.SENTRY_DSN == ""

    def test_debug_is_off(self):
        assert local_settings.DEBUG is False


class TestLocalStorage:
    def test_database_is_sqlite(self):
        assert local_settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"

    def test_database_is_not_postgres(self):
        rendered = str(local_settings.DATABASES["default"])
        assert "postgres" not in rendered.lower()


class TestNetworkPosture:
    def test_only_loopback_hosts_allowed(self):
        # This is the DNS-rebinding control: a Host header for any other name is
        # rejected by Django before a view runs.
        assert local_settings.ALLOWED_HOSTS == ["127.0.0.1", "localhost"]

    def test_cors_is_not_open(self):
        assert local_settings.CORS_ALLOW_ALL_ORIGINS is False

    def test_cors_defaults_to_sefaria_only(self):
        # Extra origins are opt-in through SEFARIA_ALLOWED_ORIGINS, which is unset
        # here; the shipped default must never admit anything else.
        assert all(
            origin.startswith("https://") and "sefaria.org" in origin
            for origin in local_settings.DEFAULT_ALLOWED_ORIGINS
        )
        assert local_settings.CORS_ALLOWED_ORIGINS == local_settings.DEFAULT_ALLOWED_ORIGINS

    def test_private_network_access_is_answered(self):
        # Without this Chrome drops the https -> localhost response silently.
        assert local_settings.CORS_ALLOW_PRIVATE_NETWORK is True


class TestUrlSurface:
    def test_uses_the_trimmed_urlconf(self):
        assert local_settings.ROOT_URLCONF == URLCONF

    def test_serves_what_local_mode_needs(self):
        for path in SERVED:
            assert resolve(path, urlconf=URLCONF), path

    def test_does_not_serve_server_side_routes(self):
        for path in NOT_SERVED:
            try:
                resolve(path, urlconf=URLCONF)
            except Resolver404:
                continue
            raise AssertionError(f"{path} should not be served by the local runner")
