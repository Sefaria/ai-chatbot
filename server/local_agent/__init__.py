"""Sefaria local agent — Claude Code, the Sefaria MCP, and a thin daemon.

The chat UI on sefaria.org talks to this process on loopback; this process runs
Claude Code against the public Sefaria MCP server. Deliberately *not* a copy of
the hosted agent: Claude Code is the agent, so there is no turn orchestration,
no prompt pipeline, no summarisation and no tool layer here.

What that buys, compared with reusing the server's own agent:

* No Django, so no settings or ``.env`` to inherit by accident.
* Conversation memory is the SDK session, so no summary machinery.
* Tools come from the MCP server, so they track it without redeployment.

Completed turns are posted to sefaria.org, so history stays in the same tables
the hosted chat uses and reads back on any device.
"""

import sys

MINIMUM_PYTHON = (3, 11)

if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit(
        f"The Sefaria local agent needs Python "
        f"{MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]} or newer, but this is "
        f"{sys.version_info.major}.{sys.version_info.minor} ({sys.executable}).\n"
        "If the server virtualenv is set up, run it with that interpreter:\n"
        "    ./venv/bin/python -m local_agent"
    )

DEFAULT_PORT = 8899
