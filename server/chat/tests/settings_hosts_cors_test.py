"""Host and CORS settings for chatbot_server.settings.

Settings are imported in a subprocess so each case gets a fresh interpreter and
its own environment. The subprocess blocks the dotenv import, so a developer's
local server/.env never fills in a variable the case leaves unset.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parents[2]
HOST_CORS_VARS = (
    "DJANGO_ALLOWED_HOSTS",
    "CHATBOT_CORS_ALLOWED_ORIGINS",
    "DJANGO_DEBUG",
    "DJANGO_SECRET_KEY",
    "CHATBOT_USER_TOKEN_SECRET",
)
VALID_SECRET_KEY = "a-long-random-django-secret-key-for-tests"
VALID_TOKEN_SECRET = "a-long-random-token-secret-for-tests"

_PRINT_SETTINGS = (
    "import json, sys; sys.modules['dotenv'] = None; import chatbot_server.settings as s; "
    "print(json.dumps({'hosts': s.ALLOWED_HOSTS, 'allow_all': s.CORS_ALLOW_ALL_ORIGINS, "
    "'origins': s.CORS_ALLOWED_ORIGINS, 'credentials': s.CORS_ALLOW_CREDENTIALS}))"
)


def _load_settings(overrides):
    env = {k: v for k, v in os.environ.items() if k not in HOST_CORS_VARS}
    env.update({"DJANGO_DEBUG": "True"})
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    result = subprocess.run(
        [sys.executable, "-c", _PRINT_SETTINGS],
        cwd=SERVER_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


def test_hosts_default_to_any_when_unset():
    settings = _load_settings({"DJANGO_ALLOWED_HOSTS": None})
    assert settings["hosts"] == ["*"]


def test_hosts_read_from_comma_separated_env():
    settings = _load_settings({"DJANGO_ALLOWED_HOSTS": " api.example.org , internal.example.org ,"})
    assert settings["hosts"] == ["api.example.org", "internal.example.org"]


def test_cors_origins_default_to_allow_all_when_unset():
    settings = _load_settings({"CHATBOT_CORS_ALLOWED_ORIGINS": None})
    assert settings["allow_all"] is True
    assert settings["origins"] == []


def test_cors_origins_restrict_when_set():
    settings = _load_settings(
        {"CHATBOT_CORS_ALLOWED_ORIGINS": "https://www.example.org, https://staging.example.org"}
    )
    assert settings["allow_all"] is False
    assert settings["origins"] == ["https://www.example.org", "https://staging.example.org"]


@pytest.mark.parametrize("debug", ["True", "False"])
def test_cors_credentials_are_not_sent(debug):
    overrides = {"DJANGO_DEBUG": debug}
    if debug == "False":
        overrides.update(
            {"DJANGO_SECRET_KEY": VALID_SECRET_KEY, "CHATBOT_USER_TOKEN_SECRET": VALID_TOKEN_SECRET}
        )
    settings = _load_settings(overrides)
    assert settings["credentials"] is False
