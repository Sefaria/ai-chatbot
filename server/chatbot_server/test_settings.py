"""
Test-specific Django settings.
Imports base settings and overrides database to use SQLite in-memory.
"""

from .settings import *  # noqa: F401, F403

# Override database to use SQLite in-memory for tests
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
