"""
Claude Agent with Sefaria tool calling.

Exports:
- ClaudeAgentService: Main agent runtime
- ConversationMessage, AgentResponse, AgentProgressUpdate: Data types
- Tool schemas and utilities
- SefariaClient and tool executor
"""

from .claude_service import (
    ClaudeAgentService,
    get_agent_service,
)
from .contracts import AgentProgressUpdate, AgentResponse, ConversationMessage, MessageContext
from .sefaria_client import SefariaClient
from .tool_executor import SefariaToolExecutor
from .tool_schemas import ALL_TOOLS, SEFARIA_TOOL_SCHEMAS, get_all_tools, get_tools_by_names

__all__ = [
    "ClaudeAgentService",
    "get_agent_service",
    "ConversationMessage",
    "MessageContext",
    "AgentResponse",
    "AgentProgressUpdate",
    "SEFARIA_TOOL_SCHEMAS",
    "ALL_TOOLS",
    "get_all_tools",
    "get_tools_by_names",
    "SefariaClient",
    "SefariaToolExecutor",
]
