"""
Claude Agent with Sefaria tool calling.

Exports:
- ClaudeAgentService: Main agent runtime
- ConversationMessage, AgentResponse, AgentProgressUpdate: Data types
- Tool schemas and utilities
- SefariaClient and tool executor

Imports are resolved lazily via ``__getattr__`` so that importing the tool layer
(schemas, executor, HTTP client) does not pull in ``claude_agent_sdk``,
``anthropic`` or ``braintrust``. The standalone MCP server in ``mcp_server``
depends on that: it needs the tools without the agent runtime.
"""

from typing import TYPE_CHECKING, Any

_EXPORTS = {
    "CatalogService": ".catalog_service",
    "ClaudeAgentService": ".claude_service",
    "AgentConfig": ".contracts",
    "AgentProgressUpdate": ".contracts",
    "AgentResponse": ".contracts",
    "ConversationMessage": ".contracts",
    "MessageContext": ".contracts",
    "TurnCancelled": ".contracts",
    "SefariaClient": ".sefaria_client",
    "SefariaToolExecutor": ".tool_executor",
    "ALL_TOOLS": ".tool_schemas",
    "SEFARIA_TOOL_SCHEMAS": ".tool_schemas",
    "get_all_tools": ".tool_schemas",
    "get_tools_by_names": ".tool_schemas",
    "get_tools_for_labs": ".tool_schemas",
    "get_tools_for_surface": ".tool_schemas",
    "has_surface": ".tool_schemas",
}

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from .catalog_service import CatalogService
    from .claude_service import ClaudeAgentService
    from .contracts import (
        AgentConfig,
        AgentProgressUpdate,
        AgentResponse,
        ConversationMessage,
        MessageContext,
        TurnCancelled,
    )
    from .sefaria_client import SefariaClient
    from .tool_executor import SefariaToolExecutor
    from .tool_schemas import (
        ALL_TOOLS,
        SEFARIA_TOOL_SCHEMAS,
        get_all_tools,
        get_tools_by_names,
        get_tools_for_labs,
        get_tools_for_surface,
        has_surface,
    )


def __getattr__(name: str) -> Any:
    """Import an export on first access (PEP 562)."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from importlib import import_module

    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(_EXPORTS)


__all__ = [
    "ALL_TOOLS",
    "AgentConfig",
    "AgentProgressUpdate",
    "AgentResponse",
    "CatalogService",
    "ClaudeAgentService",
    "ConversationMessage",
    "MessageContext",
    "SEFARIA_TOOL_SCHEMAS",
    "SefariaClient",
    "SefariaToolExecutor",
    "TurnCancelled",
    "get_all_tools",
    "get_tools_by_names",
    "get_tools_for_labs",
    "get_tools_for_surface",
    "has_surface",
]

assert sorted(__all__) == sorted(_EXPORTS), "__all__ and _EXPORTS have drifted"
