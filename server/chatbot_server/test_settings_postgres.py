"""
Test settings using PostgreSQL (same as production).
Use this in CI to catch database-specific issues.

Usage: pytest --ds=chatbot_server.test_settings_postgres

Local setup:
  createdb ai_chatbot_test
  # Or let Django create it: createuser -s $(whoami) if needed
"""

import os

from .settings import *  # noqa: F401, F403

# PostgreSQL config with defaults for local testing
# CI will override these via environment variables
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "ai_chatbot_test"),
        "USER": os.environ.get("DB_USER", ""),  # Empty = use system user
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}
