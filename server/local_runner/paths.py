"""Filesystem locations the runner owns.

The runner owns its data directory and the database inside it. Nothing the agent
can call takes a path (see D2 in the plan) — this module is the only place that
decides where local state lives.

Resolving a path never creates it: importing the settings module must not touch
the filesystem. ``ensure_data_dir`` is called once at startup instead.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_HOME = "SEFARIA_AGENT_HOME"


def data_dir() -> Path:
    """Return the runner's data directory."""
    configured = os.environ.get(ENV_HOME)
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".sefaria-agent"


def database_path() -> Path:
    """Return the SQLite file backing local conversation history."""
    return data_dir() / "local.sqlite3"


def ensure_data_dir() -> Path:
    """Create the data directory if it does not exist, and return it."""
    path = data_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path
