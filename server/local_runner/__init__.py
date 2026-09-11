"""Local agent runner — the Django host that runs on a user's own machine.

See docs/plans/2026-09-08-local-agent-runner.md. This package is a settings
profile and entrypoint, not a second web framework: it reuses the chat app's
views so the SSE contract, cancellation and recovery paths are the production
ones by construction.
"""

import sys

# Checked before anything else in the package imports, so an old interpreter
# reports itself rather than surfacing as an obscure ImportError from whichever
# module first uses a modern stdlib name.
MINIMUM_PYTHON = (3, 11)

if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit(
        f"The Sefaria agent runner needs Python "
        f"{MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]} or newer, but this is "
        f"{sys.version_info.major}.{sys.version_info.minor} "
        f"({sys.executable}).\n"
        "If the server virtualenv is set up, run it with that interpreter:\n"
        "    ./venv/bin/python -m local_runner"
    )

DEFAULT_PORT = 8899
