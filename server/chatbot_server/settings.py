"""
Django settings for chatbot_server project.
"""

import os
from pathlib import Path

from corsheaders.defaults import default_headers

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
    "rest_framework",
    "chat",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
]

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
STATIC_URL = "static/"

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

# CORS headers for SSE streaming (include Sentry tracing headers)
CORS_ALLOW_HEADERS = list(default_headers) + [
    "sentry-trace",
    "baggage",
]


# Allow credentials for CORS
CORS_ALLOW_CREDENTIALS = True

# Expose headers needed for SSE
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

# Chat turn limit (1 = single Q&A, higher = multi-turn conversation)
MAX_TURNS = max(1, int(os.environ.get("MAX_TURNS", 1)))

# ============================================================================
# Anthropic API Configuration
# ============================================================================
# Set ANTHROPIC_API_KEY environment variable
#
# AI Router & Guardrails Configuration:
# - ROUTER_USE_AI=true/false (default: true) - Enable AI-based flow routing
# - GUARDRAILS_USE_AI=true/false (default: true) - Enable AI-based guardrails
# - ROUTER_MODEL=claude-3-5-haiku-20241022 (default) - Model for routing
# - GUARDRAIL_MODEL=claude-3-5-haiku-20241022 (default) - Model for guardrails
#
# Note: Uses Claude 3.5 Haiku by default for speed and cost-effectiveness.
# Falls back to rule-based classification if AI fails or is disabled.

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
        "chat.router": {
            "handlers": ["chat_console"],
            "level": "INFO",
            "propagate": False,
        },
        "chat.router.guardrails": {
            "handlers": ["chat_console"],
            "level": "WARNING",
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
