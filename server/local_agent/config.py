"""Everything the daemon reads from its environment, in one place.

Deliberately explicit rather than inherited: the previous design pulled Django
settings in and picked up whatever ``server/.env`` happened to hold, which is
how a stale ANTHROPIC_API_KEY silently replaced subscription auth.
"""

from __future__ import annotations

import os

DEFAULT_CHATBOT_URL = "https://chat.sefaria.org"
DEFAULT_MCP_URL = "https://mcp.sefaria.org/sse"
DEFAULT_MODEL = "claude-sonnet-4-6"

DEFAULT_ALLOWED_ORIGINS = (
    "https://www.sefaria.org",
    "https://sefaria.org",
    "https://www.sefaria.org.il",
    "https://sefaria.org.il",
    "https://staging.sefaria.org",
)

MCP_SERVER_NAME = "sefaria"


def chatbot_url() -> str:
    """Sefaria's chatbot server: prompts, the guardrail, and history."""
    return os.environ.get("SEFARIA_CHATBOT_URL", DEFAULT_CHATBOT_URL).rstrip("/")


def mcp_url() -> str:
    """The Sefaria MCP server the agent gets its tools from."""
    return os.environ.get("SEFARIA_MCP_URL", DEFAULT_MCP_URL).rstrip("/")


def mcp_server_config() -> dict[str, str]:
    """Transport config for the MCP URL.

    Inferred from the path because the two transports are not interchangeable
    and the deployed servers do not agree: production currently serves ``/sse``
    only, while a locally run ``python -m mcp_server`` serves both.
    """
    url = mcp_url()
    transport = "sse" if url.endswith("/sse") else "http"
    return {"type": transport, "url": url}


def model() -> str:
    return os.environ.get("SEFARIA_AGENT_MODEL", DEFAULT_MODEL)


def allowed_origins() -> list[str]:
    """Origins permitted to reach the daemon.

    Extras are opt-in, for pointing a local build at it; the shipped default
    admits sefaria.org and sefaria.org.il only.
    """
    extra = os.environ.get("SEFARIA_ALLOWED_ORIGINS", "")
    return [*DEFAULT_ALLOWED_ORIGINS, *(o.strip() for o in extra.split(",") if o.strip())]


def port() -> int:
    return int(os.environ.get("SEFARIA_AGENT_PORT", "8899"))


def history_enabled() -> bool:
    """Whether completed turns are posted to sefaria.org for chat history."""
    return os.environ.get("SEFARIA_AGENT_HISTORY", "true").lower() != "false"
