"""The shared tool layer must import without Django.

``requirements-mcp.txt`` builds the public MCP server from a dependency set that
deliberately excludes Django:

    the shared tool layer under chat/V2/agent depends only on httpx and stdlib

Nothing enforced that, so a stray import in the tool layer would only surface as a
broken MCP image. These are the modules ``mcp_server/app.py`` imports.

Runs in a subprocess: blocking ``django`` on ``sys.meta_path`` would corrupt module
state for the rest of the session if done in-process.
"""

import subprocess
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[2]

TOOL_LAYER_MODULES = [
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
            raise ImportError("tool layer imported django: " + name)
        return None


sys.meta_path.insert(0, DjangoBlocker())

for module in {modules!r}:
    importlib.import_module(module)

print("OK")
"""


def test_tool_layer_imports_without_django():
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT.format(modules=TOOL_LAYER_MODULES)],
        cwd=SERVER_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "The tool layer gained a Django dependency, which breaks the MCP server image.\n"
        "See requirements-mcp.txt.\n\n" + result.stderr
    )
    assert "OK" in result.stdout
