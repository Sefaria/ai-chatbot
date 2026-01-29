"""
Django settings for chatbot_server project.
"""

import os
from pathlib import Path

from corsheaders.defaults import default_headers

try:
    import whitenoise  # noqa: F401

    _WHITENOISE_AVAILABLE = True
except ImportError:
    _WHITENOISE_AVAILABLE = False

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file
# Loads from server/.env (same directory as manage.py)
try:
    from dotenv import load_dotenv

    # Load .env from the server directory (parent of chatbot_server)
    env_path = BASE_DIR / ".env"
    load_dotenv(dotenv_path=env_path)
except ImportError:
    pass

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-dev-key-change-in-production")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get("DJANGO_DEBUG", "True").lower() == "true"

ALLOWED_HOSTS = ["*"]

# Application definition
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "corsheaders",
    "channels",
    "rest_framework",
    "chat",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
]

if _WHITENOISE_AVAILABLE:
    MIDDLEWARE.append("whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "chatbot_server.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

WSGI_APPLICATION = "chatbot_server.wsgi.application"
ASGI_APPLICATION = "chatbot_server.asgi.application"

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

# Database - using postgres for production
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME"),
        "USER": os.environ.get("DB_USER"),
        "PASSWORD": os.environ.get("DB_PASSWORD"),
        "HOST": os.environ.get("DB_HOST"),
        "PORT": os.environ.get("DB_PORT"),
    }
}

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Static files
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_URL = "/static/"

# WhiteNoise configuration for serving static files in production
if _WHITENOISE_AVAILABLE:
    STORAGES = {
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# CORS settings - allow all origins in development
# Set to True to allow any origin (for local development)
CORS_ALLOW_ALL_ORIGINS = True

# Fallback list if CORS_ALLOW_ALL_ORIGINS is False

# Allow all methods
CORS_ALLOW_METHODS = [
    "DELETE",
    "GET",
    "OPTIONS",
    "PATCH",
    "POST",
    "PUT",
]

# CORS headers (include Sentry tracing headers)
CORS_ALLOW_HEADERS = list(default_headers) + [
    "sentry-trace",
    "baggage",
]


# Allow credentials for CORS
CORS_ALLOW_CREDENTIALS = True

# Expose headers for API responses
CORS_EXPOSE_HEADERS = [
    "Content-Type",
    "Cache-Control",
    "X-Accel-Buffering",
]

# Allow preflight requests to be cached
CORS_PREFLIGHT_MAX_AGE = 86400

# REST Framework settings
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
    ],
    "UNAUTHENTICATED_USER": None,
}

# ============================================================================
# LangSmith Tracing Configuration
# ============================================================================
# Set these environment variables to enable LangSmith tracing:
# - LANGSMITH_API_KEY=<your-api-key>
# - LANGSMITH_PROJECT=sefaria-chatbot (optional, defaults to this)
# - LANGSMITH_ENDPOINT=https://api.smith.langchain.com (optional)

# ============================================================================
# Braintrust Configuration (Prompts + Evals)
# ============================================================================
# Set these environment variables to enable Braintrust:
# - BRAINTRUST_API_KEY=<your-api-key>
# - BRAINTRUST_PROJECT=sefaria-chatbot (optional, defaults to this)

# Environment tag for logging
ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")

# Prompt slug defaults (Braintrust)
CORE_PROMPT_SLUG = os.environ.get("CORE_PROMPT_SLUG", "core-8fbc")

# ============================================================================
# Chat User Token Configuration
# ============================================================================
# Token secret used to decrypt incoming userId values for chat requests.
CHATBOT_USER_TOKEN_SECRET = os.environ.get("CHATBOT_USER_TOKEN_SECRET", "secret")

# ============================================================================
# Anthropic API Configuration
# ============================================================================
# Set ANTHROPIC_API_KEY environment variable
#
# Note: Uses Claude Sonnet by default for the main agent.

# ============================================================================
# Sefaria API Configuration (optional)
# ============================================================================
# - SEFARIA_API_BASE_URL (default: https://www.sefaria.org)
# - SEFARIA_AI_BASE_URL (default: https://ai.sefaria.org)
# - SEFARIA_AI_TOKEN (for authenticated requests)
# - VIRTUAL_HAVRUTA_HTTP_SERVICE_HOST (for internal deployment)
# - VIRTUAL_HAVRUTA_HTTP_SERVICE_PORT (for internal deployment)

# Logging configuration
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
        "chat": {
            "format": "{asctime} | {message}",
            "style": "{",
            "datefmt": "%H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "chat_console": {
            "class": "logging.StreamHandler",
            "formatter": "chat",
        },
    },
    "loggers": {
        "chat": {
            "handlers": ["chat_console"],
            "level": "INFO",
            "propagate": False,
        },
        "chat.agent": {
            "handlers": ["chat_console"],
            "level": "INFO",
            "propagate": False,
        },
        "chat.prompts": {
            "handlers": ["chat_console"],
            "level": "INFO",
            "propagate": False,
        },
        "chat.tracing": {
            "handlers": ["chat_console"],
            "level": "INFO",
            "propagate": False,
        },
        "chat.logging": {
            "handlers": ["chat_console"],
            "level": "INFO",
            "propagate": False,
        },
        "chat.summarization": {
            "handlers": ["chat_console"],
            "level": "INFO",
            "propagate": False,
        },
        "django": {
            "handlers": ["console"],
            "level": "INFO",
        },
    },
}
