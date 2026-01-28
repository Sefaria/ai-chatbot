"""
Claude Agent with Sefaria tool calling and routed flows.

Exports:
- ClaudeAgentService: Main agent runtime
- ConversationMessage, AgentResponse, AgentProgressUpdate: Data types
- Tool schemas and utilities
- SefariaClient and tool executor
"""

from .claude_service import (
    AgentProgressUpdate,
    AgentResponse,
    ClaudeAgentService,
    ConversationMessage,
    get_agent_service,
)
from .sefaria_client import SefariaClient
from .tool_executor import SefariaToolExecutor
from .tool_schemas import (
    ALL_TOOLS,
    DEEP_ENGAGEMENT_TOOL_NAMES,
    DISCOVERY_TOOL_NAMES,
    TRANSLATION_TOOL_NAMES,
    SEFARIA_TOOL_SCHEMAS,
    get_tools_by_names,
    get_tools_for_flow,
)

__all__ = [
    # Agent service
    "ClaudeAgentService",
    "get_agent_service",
    # Data types
    "ConversationMessage",
    "AgentResponse",
    "AgentProgressUpdate",
    # Tool schemas
    "SEFARIA_TOOL_SCHEMAS",
    "ALL_TOOLS",
    "get_tools_for_flow",
    "get_tools_by_names",
    "TRANSLATION_TOOL_NAMES",
    "DISCOVERY_TOOL_NAMES",
    "DEEP_ENGAGEMENT_TOOL_NAMES",
    # Sefaria client
    "SefariaClient",
    "SefariaToolExecutor",
]
