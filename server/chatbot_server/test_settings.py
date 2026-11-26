"""
Test-specific Django settings.
Imports base settings and overrides database to use SQLite in-memory.
"""

from .settings import *  # noqa: F401, F403

BRAINTRUST_LOGGING_ENABLED = True

# Override database to use SQLite in-memory for tests
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Tests use the bundled prompt files; remote-loader tests opt in with override_settings and a mocked prompt service.
PERSONALIZATION_PROMPT_SLUG_ANSWER = ""
PERSONALIZATION_PROMPT_SLUG_PROFILE_USED = ""
PERSONALIZATION_PROMPT_SLUG_PROFILE_UPDATE = ""
PERSONALIZATION_PROMPT_SLUG_PROFILE_ALTER = ""
