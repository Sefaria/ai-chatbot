"""FastMCP application for the public Sefaria MCP server.

Served at ``https://chat.sefaria.org/mcp`` alongside the old standalone server on
``mcp.sefaria.org``, which keeps running unchanged until it is deprecated. See
``docs/plans/2026-10-18-mcp-consolidation.md``.

- ``/mcp``  Streamable HTTP, stateless. No SSE: SSE clients stay on the old server.
- ``/mcp/healthz``  liveness/readiness probe.
- ``/.well-known/oauth-protected-resource/mcp``  only when ``SEFARIA_MCP_DISCOVERY=stub``.

The gateway routes only ``/mcp*`` and ``/.well-known/oauth-protected-resource/mcp*`` on
the chat host to this server; everything else on that host is the Django app.
"""

from __future__ import annotations

import contextlib
import logging
import os

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from chat.V2.agent.tool_executor import SefariaToolExecutor
from chat.V2.agent.tool_schemas import get_tools_for_surface

from .tool import SefariaTool

logger = logging.getLogger("mcp_server")

SURFACE = "mcp"
HTTP_PATH = "/mcp"
HEALTH_PATH = "/mcp/healthz"
PROTECTED_RESOURCE_PATH = "/.well-known/oauth-protected-resource/mcp"
SERVER_NAME = "Sefaria MCP 📚"

DEFAULT_PORT = 8088
DEFAULT_PUBLIC_URL = "https://chat.sefaria.org/mcp"
DEFAULT_ALLOWED_HOSTS = ["chat.sefaria.org", "chat-dev.sefaria.org"]
DEFAULT_ALLOWED_ORIGINS = ["https://chat.sefaria.org", "https://chat-dev.sefaria.org"]
DISCOVERY_MODES = ("none", "stub")


def _env_int(name: str, default: int) -> int:
    """Read an int from the environment, falling back if unset or unparseable."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using %s", name, raw, default)
        return default


def _env_list(name: str, default: list[str]) -> list[str]:
    """Read a comma-separated list, falling back if unset or empty.

    An empty value must fall back rather than yield []: FastMCP treats an explicit
    allowlist as authoritative, so [] would make /mcp loopback-only and reject every
    real request with 421. A blank ConfigMap value is an easy way to get here.
    """
    raw = os.environ.get(name)
    items = [item.strip() for item in raw.split(",") if item.strip()] if raw else []
    if not items:
        if raw:
            logger.warning("Ignoring empty %s=%r; using %s", name, raw, default)
        return default
    return items


def server_version() -> str:
    """Release tag of this deployment, reported as ``serverInfo.version``."""
    return os.environ.get("SEFARIA_MCP_VERSION", "0.0.0-dev")


def public_url() -> str:
    return os.environ.get("SEFARIA_MCP_PUBLIC_URL", DEFAULT_PUBLIC_URL).rstrip("/")


def discovery_mode() -> str:
    """How OAuth discovery is answered while /mcp is authless.

    ``none`` serves nothing, which RFC 9728 reads as "no auth required". ``stub``
    serves an empty protected-resource document, which is what claude.ai's Connect
    button needed on the old server (sefaria-mcp PRs #25 -> #28). Which one this
    endpoint needs is measured on dev; until then the proven ``stub`` is the default.
    """
    mode = os.environ.get("SEFARIA_MCP_DISCOVERY", "stub").strip().lower()
    if mode not in DISCOVERY_MODES:
        logger.warning("Ignoring invalid SEFARIA_MCP_DISCOVERY=%r; using stub", mode)
        return "stub"
    return mode


def host_protection_enabled() -> bool:
    """Escape hatch: ``SEFARIA_MCP_HOST_PROTECTION=off`` disables the guard without a redeploy."""
    return os.environ.get("SEFARIA_MCP_HOST_PROTECTION", "on").strip().lower() != "off"


def port() -> int:
    return _env_int("SEFARIA_MCP_PORT", DEFAULT_PORT)


def build_server(executor: SefariaToolExecutor | None = None) -> FastMCP:
    """Build the FastMCP server with every tool marked for the MCP surface."""
    executor = executor or SefariaToolExecutor()
    mcp = FastMCP(SERVER_NAME, version=server_version())

    for schema in get_tools_for_surface(SURFACE):
        mcp.add_tool(SefariaTool.from_schema(schema, executor))

    return mcp


async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def build_app(executor: SefariaToolExecutor | None = None) -> Starlette:
    """ASGI app serving stateless Streamable HTTP at /mcp."""
    protect = host_protection_enabled()
    if not protect:
        logger.warning("SEFARIA_MCP_HOST_PROTECTION=off: /mcp accepts any Host and Origin")

    # Streamable HTTP must validate Host and Origin against DNS rebinding. FastMCP
    # answers an unlisted Host with 421 and an unlisted Origin with 403, and always
    # admits loopback hosts. Origins are listed explicitly because the pod is reached
    # over plain HTTP behind the gateway, so FastMCP's same-origin fallback would
    # compare "https://chat.sefaria.org" with "http://chat.sefaria.org" and reject it.
    #
    # Stateless: no session state in pod memory, so rollouts drop nothing and the
    # Deployment can run several replicas. /mcp serves POST and DELETE only.
    mcp_app = build_server(executor).http_app(
        transport="http",
        path=HTTP_PATH,
        stateless_http=True,
        host_origin_protection=protect,
        allowed_hosts=(
            _env_list("SEFARIA_MCP_ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS) if protect else None
        ),
        allowed_origins=(
            _env_list("SEFARIA_MCP_ALLOWED_ORIGINS", DEFAULT_ALLOWED_ORIGINS) if protect else None
        ),
    )

    # Only the protected-resource document at our own path is ours to vary: the
    # host-root /.well-known/* paths on the chat host belong to Django.
    routes = [Route(HEALTH_PATH, healthz, methods=["GET"])]
    if discovery_mode() == "stub":
        document = {"resource": public_url(), "authorization_servers": []}

        async def protected_resource(request: Request) -> JSONResponse:
            return JSONResponse(document)

        routes.append(Route(PROTECTED_RESOURCE_PATH, protected_resource, methods=["GET"]))

    # Route, not Mount: Mount strips its prefix, so "/mcp" would reach the sub-app as
    # an empty path and 404. No method list either, so the sub-app answers
    # unsupported methods with the 405 the spec calls for rather than a 404, which
    # would tell a probing client there is no endpoint. The sub-app keeps its own
    # middleware, which scopes the Host/Origin guard to /mcp and leaves the probe
    # (whose Host is the pod IP) and the discovery document unguarded.
    routes.append(Route(HTTP_PATH, endpoint=mcp_app))

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        # Starlette does not run sub-app lifespans, and the Streamable HTTP app
        # builds its session manager in its own.
        async with mcp_app.lifespan(app):
            yield

    app = Starlette(routes=routes, lifespan=lifespan)
    app.router.redirect_slashes = False
    return app
