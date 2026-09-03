"""FastMCP application for the public Sefaria MCP server.

Serves both transports:

- ``/mcp``  Streamable HTTP — the current MCP transport.
- ``/sse``  SSE — deprecated in the spec, but what every existing connector is
            configured against. It stays.

The server is authless. It deliberately serves **nothing** on the
``/.well-known/oauth-*`` paths: under RFC 9728 the absence of those documents means
"no auth required", whereas the empty stubs the previous server returned convinced
claude.ai that the server was OAuth-protected and broke connector setup.
"""

from __future__ import annotations

import os

from fastmcp import FastMCP
from starlette.applications import Starlette

from chat.V2.agent.tool_executor import SefariaToolExecutor
from chat.V2.agent.tool_schemas import get_tools_for_surface

from .tool import SefariaTool

SURFACE = "mcp"
HTTP_PATH = "/mcp"
SSE_PATH = "/sse"
SERVER_NAME = "Sefaria MCP 📚"

DEFAULT_PORT = 8088


def server_version() -> str:
    """Release tag of this deployment.

    Set explicitly so ``serverInfo.version`` reports the Sefaria release rather
    than the FastMCP library version, which is what the old server reported.
    """
    return os.environ.get("SEFARIA_MCP_VERSION", "0.0.0-dev")


def build_server(executor: SefariaToolExecutor | None = None) -> FastMCP:
    """Build the FastMCP server with every tool marked for the MCP surface."""
    executor = executor or SefariaToolExecutor()
    mcp = FastMCP(SERVER_NAME, version=server_version())

    for schema in get_tools_for_surface(SURFACE):
        mcp.add_tool(SefariaTool.from_schema(schema, executor))

    return mcp


def build_app(executor: SefariaToolExecutor | None = None) -> Starlette:
    """ASGI app serving Streamable HTTP at /mcp and SSE at /sse."""
    mcp = build_server(executor)

    # Each transport builds its own routes: "/mcp" for Streamable HTTP, and
    # "/sse" plus the "/messages" mount SSE needs for client-to-server posts.
    # Serve them from one app so both live at the paths clients expect.
    app = mcp.http_app(transport="http", path=HTTP_PATH)
    sse_app = mcp.http_app(transport="sse", path=SSE_PATH)
    app.router.routes.extend(sse_app.routes)

    # `/sse` must not redirect to `/sse/`; existing connectors use the exact path.
    app.router.redirect_slashes = False
    return app


def port() -> int:
    return int(os.environ.get("SEFARIA_MCP_PORT", DEFAULT_PORT))
