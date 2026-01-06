"""
Tool executor for Sefaria agent tools.
"""

import json
import logging
from typing import Any, Optional
from dataclasses import dataclass

from .sefaria_client import SefariaClient

logger = logging.getLogger('chat.agent')


@dataclass
class ToolResult:
    """Result from executing a tool."""
    content: list[dict[str, Any]]
    is_error: bool = False


class SefariaToolExecutor:
    """
    Executes Sefaria tools based on Claude's tool calls.
    """
    
    def __init__(self, client: Optional[SefariaClient] = None):
        self.client = client or SefariaClient()
    
    async def execute(self, tool_name: str, tool_input: dict[str, Any]) -> ToolResult:
        """
        Execute a tool and return the result.
        
        Args:
            tool_name: Name of the tool to execute
            tool_input: Input parameters for the tool
            
        Returns:
            ToolResult with content and error status
        """
        try:
            result = await self._dispatch(tool_name, tool_input)
            return self._wrap(result)
        except Exception as e:
            logger.error(f"Tool execution error for {tool_name}: {e}")
            return self._wrap_error(str(e))
    
    async def _dispatch(self, tool_name: str, input_data: dict[str, Any]) -> Any:
        """Dispatch to the appropriate tool method."""
        
        if tool_name == 'get_text':
            return await self.client.get_text(
                input_data['reference'],
                input_data.get('version_language')
            )
        
        elif tool_name == 'text_search':
            return await self.client.text_search(
                input_data['query'],
                input_data.get('filters'),
                input_data.get('size', 10)
            )
        
        elif tool_name == 'get_current_calendar':
            return await self.client.get_current_calendar()
        
        elif tool_name == 'english_semantic_search':
            return await self.client.english_semantic_search(
                input_data['query'],
                input_data.get('filters')
            )
        
        elif tool_name == 'get_links_between_texts':
            return await self.client.get_links_between_texts(
                input_data['reference'],
                input_data.get('with_text', '0')
            )
        
        elif tool_name == 'search_in_book':
            return await self.client.search_in_book(
                input_data['query'],
                input_data['book_name'],
                input_data.get('size', 10)
            )
        
        elif tool_name == 'search_in_dictionaries':
            return await self.client.search_in_dictionaries(input_data['query'])
        
        elif tool_name == 'get_english_translations':
            return await self.client.get_english_translations(input_data['reference'])
        
        elif tool_name == 'get_topic_details':
            return await self.client.get_topic_details(
                input_data['topic_slug'],
                input_data.get('with_links', False),
                input_data.get('with_refs', False)
            )
        
        elif tool_name == 'clarify_name_argument':
            return await self.client.clarify_name_argument(
                input_data['name'],
                input_data.get('limit'),
                input_data.get('type_filter')
            )
        
        elif tool_name == 'clarify_search_path_filter':
            filter_path = await self.client.clarify_search_path_filter(
                input_data['book_name']
            )
            return {'filter_path': filter_path}
        
        elif tool_name == 'get_text_or_category_shape':
            return await self.client.get_text_or_category_shape(input_data['name'])
        
        elif tool_name == 'get_text_catalogue_info':
            return await self.client.get_text_catalogue_info(input_data['title'])
        
        elif tool_name == 'get_available_manuscripts':
            return await self.client.get_available_manuscripts(input_data['reference'])
        
        elif tool_name == 'get_manuscript_image':
            return await self.client.get_manuscript_image(
                input_data['image_url'],
                input_data.get('manuscript_title')
            )
        
        else:
            raise ValueError(f"Unknown tool: {tool_name}")
    
    def _wrap(self, payload: Any) -> ToolResult:
        """Wrap a successful result."""
        if isinstance(payload, str):
            text = payload
        else:
            text = json.dumps(payload, indent=2, ensure_ascii=False)
        
        return ToolResult(
            content=[{'type': 'text', 'text': text}],
            is_error=False
        )
    
    def _wrap_error(self, message: str) -> ToolResult:
        """Wrap an error result."""
        return ToolResult(
            content=[{'type': 'text', 'text': json.dumps({'error': message})}],
            is_error=True
        )


def describe_tool_call(tool_name: str, tool_input: dict[str, Any]) -> str:
    """
    Generate a human-readable description of a tool call.
    
    Args:
        tool_name: Name of the tool
        tool_input: Input parameters
        
    Returns:
        Human-readable description
    """
    def q(v: Any, max_len: int = 140) -> str:
        if isinstance(v, str):
            return f'"{v[:max_len]}{"…" if len(v) > max_len else ""}"'
        try:
            s = json.dumps(v)
            return s[:max_len] + ('…' if len(s) > max_len else '')
        except:
            return '[unserializable]'
    
    descriptions = {
        'text_search': lambda: f"Searching texts for {q(tool_input.get('query'))}" +
            (f" in {q(tool_input.get('filters'))}" if tool_input.get('filters') else ''),
        
        'english_semantic_search': lambda: f"Semantic search for {q(tool_input.get('query'))}",
        
        'search_in_book': lambda: f"Searching in {q(tool_input.get('book_name'))} for {q(tool_input.get('query'))}",
        
        'search_in_dictionaries': lambda: f"Searching dictionaries for {q(tool_input.get('query'))}",
        
        'get_text': lambda: f"Fetching text {q(tool_input.get('reference'))}" +
            (f" ({q(tool_input.get('version_language'))})" if tool_input.get('version_language') else ''),
        
        'get_links_between_texts': lambda: f"Finding links from {q(tool_input.get('reference'))}",
        
        'get_topic_details': lambda: f"Loading topic details for {q(tool_input.get('topic_slug'))}",
        
        'get_current_calendar': lambda: "Fetching current Jewish calendar",
        
        'clarify_name_argument': lambda: f"Clarifying name {q(tool_input.get('name'))}",
        
        'clarify_search_path_filter': lambda: f"Resolving book filter for {q(tool_input.get('book_name'))}",
        
        'get_text_or_category_shape': lambda: f"Loading shape for {q(tool_input.get('name'))}",
        
        'get_text_catalogue_info': lambda: f"Loading catalogue info for {q(tool_input.get('title'))}",
        
        'get_available_manuscripts': lambda: f"Checking available manuscripts for {q(tool_input.get('reference'))}",
        
        'get_manuscript_image': lambda: "Downloading manuscript image",
        
        'get_english_translations': lambda: f"Getting English translations for {q(tool_input.get('reference'))}"
    }
    
    if tool_name in descriptions:
        return descriptions[tool_name]()
    
    return f"Running tool {q(tool_name)} with {q(tool_input, 220)}"


