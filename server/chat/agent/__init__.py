"""
Claude Agent with Sefaria tool calling and routed flows.

Exports:
- ClaudeAgentService: Main agent runtime
- ConversationMessage, AgentResponse, AgentProgressUpdate: Data types
- Tool schemas and utilities
- SefariaClient and tool executor
"""

from .claude_service import (
    ClaudeAgentService,
    ConversationMessage,
    AgentResponse,
    AgentProgressUpdate,
    get_agent_service,
)
from .tool_schemas import (
    SEFARIA_TOOL_SCHEMAS,
    ALL_TOOLS,
    get_tools_for_flow,
    get_tools_by_names,
    HALACHIC_TOOL_NAMES,
    SEARCH_TOOL_NAMES,
    GENERAL_TOOL_NAMES,
)
from .sefaria_client import SefariaClient
from .tool_executor import SefariaToolExecutor

__all__ = [
    # Agent service
    'ClaudeAgentService',
    'get_agent_service',

    # Data types
    'ConversationMessage',
    'AgentResponse',
    'AgentProgressUpdate',

    # Tool schemas
    'SEFARIA_TOOL_SCHEMAS',
    'ALL_TOOLS',
    'get_tools_for_flow',
    'get_tools_by_names',
    'HALACHIC_TOOL_NAMES',
    'SEARCH_TOOL_NAMES',
    'GENERAL_TOOL_NAMES',

    # Sefaria client
    'SefariaClient',
    'SefariaToolExecutor',
]
