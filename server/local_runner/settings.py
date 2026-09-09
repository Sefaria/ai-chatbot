"""Django settings for the local runner.

Inherits the server settings so prompt slugs, model names, DRF config and logging
stay identical — that is what makes local mode behave like the hosted agent — then
overrides everything that must not exist on a user's machine.

Two rules govern this file:

1. **No server secrets.** ``BRAINTRUST_API_KEY`` and ``CHATBOT_USER_TOKEN_SECRET``
   are cleared explicitly rather than merely left unset, because importing the
   base settings runs ``load_dotenv`` and would otherwise pick up a developer's
   own ``.env`` and mask the difference.
2. **Loopback only.** ``ALLOWED_HOSTS`` and the CORS allowlist are the DNS-rebinding
   and cross-origin controls; the pairing bearer token is the real authentication.
"""

from __future__ import annotations

import os

from chatbot_server.settings import *  # noqa: F403
from chatbot_server.settings import BASE_DIR  # noqa: F401

from .paths import database_path

# --- Never on a user's machine -------------------------------------------------

# Cleared, not just unset: the base settings load a .env if one is present.
CHATBOT_USER_TOKEN_SECRET = ""
os.environ.pop("BRAINTRUST_API_KEY", None)

# Traces are buffered locally and replayed by our server, which holds the key.
BRAINTRUST_LOGGING_ENABLED = False

# Sentry is the server's error channel, not a user's.
SENTRY_DSN = ""

DEBUG = False

# --- Local storage -------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(database_path()),
        "OPTIONS": {"timeout": 20},
    }
}

# --- Network posture -----------------------------------------------------------

# Rejects a Host header pointing at anything but loopback, which is what stops
# DNS rebinding from reaching the daemon.
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

CORS_ALLOW_ALL_ORIGINS = False

# Where the widget is allowed to be served from. Extra origins are opt-in via
# SEFARIA_ALLOWED_ORIGINS (comma separated) so a developer can point a local
# build at the runner; without it, only sefaria.org can reach the daemon.
DEFAULT_ALLOWED_ORIGINS = [
    "https://www.sefaria.org",
    "https://sefaria.org",
    "https://staging.sefaria.org",
]
CORS_ALLOWED_ORIGINS = DEFAULT_ALLOWED_ORIGINS + [
    origin.strip()
    for origin in os.environ.get("SEFARIA_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]

# Chrome requires this on the https -> localhost preflight; without it the
# browser drops the response with no visible error.
CORS_ALLOW_PRIVATE_NETWORK = True

# Only the routes local mode serves. Notably absent: the Anthropic eval endpoint
# and the prompt-reload admin route, which are server-side concerns.
ROOT_URLCONF = "local_runner.urls"

# The runner token is checked before anything else runs, so a route added later
# is protected by default. CorsMiddleware stays first so preflights still answer.
MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "local_runner.middleware.OriginAllowlistMiddleware",
    "local_runner.middleware.RunnerAuthMiddleware",
    "django.middleware.common.CommonMiddleware",
]

# --- Identity ------------------------------------------------------------------

# The daemon cannot decrypt user tokens (that needs the server secret), so it
# authenticates the runner token and builds the Actor from the pairing record.
CHAT_AUTH_BACKEND = "local_runner.auth.authenticate_paired_request"

# --- Services that need Braintrust ---------------------------------------------

# The daemon has no Braintrust key, so these three are proxied to our server,
# which runs the real service. Keyed by class name; see chat/V2/utils.py.
# The guardrail in particular stays server-side on purpose: a brand-safety check
# the user's own machine could edit would not be one.
CHAT_SERVICE_OVERRIDES = {
    "PromptService": "local_runner.services.ProxyPromptService",
    "GuardrailService": "local_runner.services.ProxyGuardrailService",
    "RouterService": "local_runner.services.ProxyRouterService",
    "SummaryService": "local_runner.services.ProxySummaryService",
    # Not proxied — disabled. See NullAppetizerService.
    "AppetizerService": "local_runner.services.NullAppetizerService",
}
