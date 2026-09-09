"""The agent package must import without Django.

Phase 1 of the local agent runner (docs/plans/2026-09-08-local-agent-runner.md):
the agent loop takes an AgentConfig and resolves Django-backed services at call
time, so a non-Django host can import it and inject its own implementations.

This runs in a subprocess: it blocks ``django`` on ``sys.meta_path``, which would
corrupt module state for the rest of the session if done in-process.
"""

import subprocess
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[2]

# Every module a non-Django host needs to import to run one agent turn.
AGENT_MODULES = [
    "chat.V2.agent.claude_service",
    "chat.V2.agent.turn_orchestrator",
    "chat.V2.agent.guardrail_gate",
    "chat.V2.agent.router",
    "chat.V2.agent.tool_executor",
    "chat.V2.agent.tool_schemas",
    "chat.V2.agent.sefaria_client",
]

SCRIPT = """
import importlib
import importlib.abc
import sys


class DjangoBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "django" or name.startswith("django."):
            raise ImportError("agent package imported django: " + name)
        return None


sys.meta_path.insert(0, DjangoBlocker())

for module in {modules!r}:
    importlib.import_module(module)

from chat.V2.agent.contracts import AgentConfig

AgentConfig(model="m", response_format_prompt_slug="s")
print("OK")
"""


def test_agent_package_imports_without_django():
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT.format(modules=AGENT_MODULES)],
        cwd=SERVER_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "The agent package gained a Django dependency at import time.\n"
        "Resolve Django-backed services inside the call instead.\n\n" + result.stderr
    )
    assert "OK" in result.stdout
