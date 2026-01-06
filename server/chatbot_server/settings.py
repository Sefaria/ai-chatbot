"""
Django settings for chatbot_server project.
"""

from pathlib import Path
import os

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file
# Loads from server/.env (same directory as manage.py)
try:
    from dotenv import load_dotenv
    # Load .env from the server directory (parent of chatbot_server)
    env_path = BASE_DIR / '.env'
    load_dotenv(dotenv_path=env_path)
except ImportError:
    pass

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'django-insecure-dev-key-change-in-production')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DJANGO_DEBUG', 'True').lower() == 'true'

ALLOWED_HOSTS = ['*']

# Application definition
INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.staticfiles',
    'corsheaders',
    'rest_framework',
    'chat',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
]

ROOT_URLCONF = 'chatbot_server.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
            ],
        },
    },
]

WSGI_APPLICATION = 'chatbot_server.wsgi.application'

# Database - using SQLite for simplicity, switch to PostgreSQL for production
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = 'static/'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# CORS settings - allow all origins in development
CORS_ALLOW_ALL_ORIGINS = DEBUG
CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

# REST Framework settings
REST_FRAMEWORK = {
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
    ],
    'UNAUTHENTICATED_USER': None,
}

# Langfuse configuration (for tracing)
# Set these environment variables to enable Langfuse:
# - LANGFUSE_PUBLIC_KEY=<your-public-key>
# - LANGFUSE_SECRET_KEY=<your-secret-key>
# - LANGFUSE_HOST=https://cloud.langfuse.com (or https://us.cloud.langfuse.com for US region)

# Anthropic API configuration
# Set ANTHROPIC_API_KEY environment variable

# Sefaria API configuration (optional)
# - SEFARIA_API_BASE_URL (default: https://www.sefaria.org)
# - SEFARIA_AI_BASE_URL (default: https://ai.sefaria.org)
# - SEFARIA_AI_TOKEN (for authenticated requests)

# Logging configuration
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
        'chat': {
            'format': '{asctime} | {message}',
            'style': '{',
            'datefmt': '%H:%M:%S',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'chat_console': {
            'class': 'logging.StreamHandler',
            'formatter': 'chat',
        },
    },
    'loggers': {
        'chat': {
            'handlers': ['chat_console'],
            'level': 'INFO',
            'propagate': False,
        },
        'chat.agent': {
            'handlers': ['chat_console'],
            'level': 'INFO',
            'propagate': False,
        },
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
        },
    },
}
