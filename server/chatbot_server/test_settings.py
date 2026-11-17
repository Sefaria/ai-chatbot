"""
Test-specific Django settings.
Imports base settings and overrides database to use SQLite in-memory.
"""

import os

# Tests use the development secret fallbacks, which settings.py refuses outside DEBUG.
# setdefault keeps an explicit DJANGO_DEBUG from the environment.
os.environ.setdefault("DJANGO_DEBUG", "True")

from .settings import *  # noqa: F401, F403

BRAINTRUST_LOGGING_ENABLED = True

# Override database to use SQLite in-memory for tests
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
