"""Startup checks for insecure secrets in chatbot_server.settings.

Settings are imported in a subprocess so each case gets a fresh interpreter and
its own environment, the same way gunicorn or manage.py would load them. The
subprocess blocks the dotenv import (settings.py tolerates its absence), so a
developer's local server/.env never fills in a variable the case leaves unset.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parents[2]
SECRET_VARS = ("DJANGO_DEBUG", "DJANGO_SECRET_KEY", "CHATBOT_USER_TOKEN_SECRET")
VALID_SECRET_KEY = "a-long-random-django-secret-key-for-tests"
VALID_TOKEN_SECRET = "a-long-random-token-secret-for-tests"


_IMPORT_SETTINGS = (
    "import sys; sys.modules['dotenv'] = None; import chatbot_server.settings as s; print(s.DEBUG)"
)


def _import_settings(overrides):
    env = {k: v for k, v in os.environ.items() if k not in SECRET_VARS}
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        [sys.executable, "-c", _IMPORT_SETTINGS],
        cwd=SERVER_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_debug_is_off_when_unset():
    result = _import_settings(
        {
            "DJANGO_DEBUG": None,
            "DJANGO_SECRET_KEY": VALID_SECRET_KEY,
            "CHATBOT_USER_TOKEN_SECRET": VALID_TOKEN_SECRET,
        }
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


@pytest.mark.parametrize(
    "overrides, expected_name",
    [
        ({"DJANGO_SECRET_KEY": None}, "DJANGO_SECRET_KEY"),
        (
            {"DJANGO_SECRET_KEY": "django-insecure-dev-key-change-in-production"},
            "DJANGO_SECRET_KEY",
        ),
        ({"DJANGO_SECRET_KEY": ""}, "DJANGO_SECRET_KEY"),
        ({"CHATBOT_USER_TOKEN_SECRET": "secret"}, "CHATBOT_USER_TOKEN_SECRET"),
        ({"CHATBOT_USER_TOKEN_SECRET": ""}, "CHATBOT_USER_TOKEN_SECRET"),
        (
            {"CHATBOT_USER_TOKEN_SECRET": "<your-chatbot-user-token-secret-here>"},
            "CHATBOT_USER_TOKEN_SECRET",
        ),
    ],
)
def test_insecure_secret_refuses_to_start_outside_debug(overrides, expected_name):
    env = {
        "DJANGO_DEBUG": "False",
        "DJANGO_SECRET_KEY": VALID_SECRET_KEY,
        "CHATBOT_USER_TOKEN_SECRET": VALID_TOKEN_SECRET,
    }
    env.update(overrides)
    result = _import_settings(env)
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert expected_name in result.stderr


def test_unset_token_secret_refuses_to_start_outside_debug():
    result = _import_settings(
        {
            "DJANGO_DEBUG": "False",
            "DJANGO_SECRET_KEY": VALID_SECRET_KEY,
            "CHATBOT_USER_TOKEN_SECRET": None,
        }
    )
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
    assert "CHATBOT_USER_TOKEN_SECRET" in result.stderr


def test_valid_secrets_start_outside_debug():
    result = _import_settings(
        {
            "DJANGO_DEBUG": "False",
            "DJANGO_SECRET_KEY": VALID_SECRET_KEY,
            "CHATBOT_USER_TOKEN_SECRET": VALID_TOKEN_SECRET,
        }
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


def test_development_defaults_still_allowed_in_debug():
    result = _import_settings(
        {
            "DJANGO_DEBUG": "True",
            "DJANGO_SECRET_KEY": None,
            "CHATBOT_USER_TOKEN_SECRET": None,
        }
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"


def test_dockerfile_collectstatic_runs_with_scoped_debug():
    dockerfile = (SERVER_DIR.parent / "Dockerfile").read_text()
    lines = [ln for ln in dockerfile.splitlines() if "manage.py collectstatic" in ln]
    assert lines, "Dockerfile no longer runs collectstatic"
    for line in lines:
        assert line.startswith("RUN DJANGO_DEBUG=true python manage.py collectstatic"), line
    assert not any(ln.strip().startswith("ENV DJANGO_DEBUG") for ln in dockerfile.splitlines())


def test_local_compose_app_runs_in_debug():
    compose = (SERVER_DIR.parent / "docker-compose.local.yml").read_text()
    assert 'DJANGO_DEBUG: "true"' in compose
