"""
Claude Agent Service with tool calling and Langfuse tracing.
"""

import os
import json
import time
import logging
from typing import Any, Optional, Callable
from dataclasses import dataclass

import anthropic
from langfuse import Langfuse, observe, get_client, propagate_attributes

from .tool_schemas import SEFARIA_TOOL_SCHEMAS
from .tool_executor import SefariaToolExecutor, describe_tool_call
from .sefaria_client import SefariaClient

logger = logging.getLogger('chat.agent')


def _get_langfuse_base_prompt() -> Optional[str]:
    """
    Fetch the base 'core' prompt from Langfuse.
    Returns None if Langfuse is not configured or prompt not found.
    """
    try:
        public_key = os.environ.get('LANGFUSE_PUBLIC_KEY')
        secret_key = os.environ.get('LANGFUSE_SECRET_KEY')
        
        if not public_key or not secret_key:
            return None
        
        langfuse = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=os.environ.get('LANGFUSE_HOST', 'https://cloud.langfuse.com'),
        )
        
        # Get production prompt named "core"
        prompt = langfuse.get_prompt("core")
        if prompt:
            # Compile the prompt (handles variables if any)
            compiled = prompt.compile()
            logger.info("Loaded base prompt 'core' from Langfuse")
            return compiled
        else:
            logger.warning("Langfuse prompt 'core' not found")
            return None
    except Exception as e:
        logger.warning(f"Failed to fetch Langfuse prompt: {e}")
        return None


@dataclass
class AgentProgressUpdate:
    """Progress update from the agent."""
    type: str  # 'status', 'tool_start', 'tool_end'
    text: Optional[str] = None
    tool_name: Optional[str] = None
    tool_input: Optional[dict] = None
    description: Optional[str] = None
    is_error: Optional[bool] = None
    output_preview: Optional[str] = None


@dataclass
class ConversationMessage:
    """A message in the conversation."""
    role: str  # 'user' or 'assistant'
    content: str


@dataclass
class AgentResponse:
    """Response from the agent including metadata."""
    content: str
    tool_calls: list[dict[str, Any]]
    llm_calls: int
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    latency_ms: int


# Default system prompt for the agent (adapted for web/markdown output)
# This is used if no Langfuse "core" prompt is available
DEFAULT_SYSTEM_PROMPT = """You are a Jewish text scholar with access to internal Sefaria tools. Follow these guidelines:

TOOL USAGE (CRITICAL):
• You MUST use the available Sefaria tools to search for and retrieve Jewish texts
• NEVER answer questions about Jewish texts, sources, or references from memory alone
• For any question about specific texts, quotes, or sources: USE get_text, text_search, or english_semantic_search
• For questions about topics, people, or concepts: USE get_topic_details
• For calendar-related questions: USE get_current_calendar
• When users ask about connections between texts: USE get_links_between_texts
• If you're unsure which tool to use, prefer text_search or english_semantic_search first

RESPONSE REQUIREMENTS:
• Respond in the same language the user asked the question in
• Gauge user intent - provide short answers for simple questions, comprehensive analysis for complex ones
• ALL claims must be sourced and cited with Sefaria links: [Source Name](https://www.sefaria.org/Reference)
• If making unsourced claims, explicitly note: "Based on my analysis (not from a specific source):"
• CRITICAL: Provide ONLY your final scholarly response. NEVER include internal search processes, tool usage descriptions, or step-by-step research narrative
• Begin responses directly with substantive content about the topic
• FORBIDDEN PHRASES: "Let me search," "I'll gather," "Now let me," "I found," "Let me look," "I'll check," or any process descriptions
• Users should only see your final scholarly conclusions, not your research process

MARKDOWN FORMATTING:
• Use standard markdown formatting
• Headers: # Header, ## Subheader
• Bold text: **bold**
• Italic text: *italic*
• Links: [Text](https://www.sefaria.org/Reference)
• Lists: - item or 1. item
• Code blocks: ```code```
• Blockquotes: > quote"""


def _check_langfuse_enabled() -> bool:
    """Check if Langfuse credentials are available."""
    public_key = os.environ.get('LANGFUSE_PUBLIC_KEY')
    secret_key = os.environ.get('LANGFUSE_SECRET_KEY')
    
    if not public_key or not secret_key:
        logger.info("Langfuse credentials not found, tracing disabled")
        return False
    
    try:
        langfuse = get_client()
        if langfuse.auth_check():
            logger.info("Langfuse tracing enabled")
            return True
        else:
            logger.warning("Langfuse auth check failed, tracing disabled")
            return False
    except Exception as e:
        logger.warning(f"Failed to initialize Langfuse: {e}")
        return False


class ClaudeAgentService:
    """
    Claude agent service with Sefaria tool calling.
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = 'claude-sonnet-4-5-20250929',
        max_iterations: int = 10,
        max_tokens: int = 8000,
        temperature: float = 0.7
    ):
        self.api_key = api_key or os.environ.get('ANTHROPIC_API_KEY')
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY is required")
        
        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.model = model
        self.max_iterations = max_iterations
        self.max_tokens = max_tokens
        self.temperature = temperature
        
        self.sefaria_client = SefariaClient()
        self.tool_executor = SefariaToolExecutor(self.sefaria_client)
        
        # Check if Langfuse is enabled
        self.langfuse_enabled = _check_langfuse_enabled()
        
        # Load base prompt from Langfuse, falling back to default
        self.system_prompt = self._build_system_prompt()
        logger.info(f"System prompt loaded ({len(self.system_prompt)} chars)")
    
    def _build_system_prompt(self) -> str:
        """
        Build the system prompt by combining Langfuse 'core' prompt with defaults.
        """
        # Try to fetch base prompt from Langfuse
        base_prompt = _get_langfuse_base_prompt()
        
        if base_prompt:
            # Combine Langfuse prompt with tool usage and formatting instructions
            return f"""{base_prompt}

TOOL USAGE (CRITICAL):
• You MUST use the available Sefaria tools to search for and retrieve Jewish texts
• NEVER answer questions about Jewish texts, sources, or references from memory alone
• For any question about specific texts, quotes, or sources: USE get_text, text_search, or english_semantic_search
• For questions about topics, people, or concepts: USE get_topic_details
• For calendar-related questions: USE get_current_calendar
• When users ask about connections between texts: USE get_links_between_texts
• If you're unsure which tool to use, prefer text_search or english_semantic_search first

MARKDOWN FORMATTING:
• Use standard markdown formatting
• Headers: # Header, ## Subheader
• Bold text: **bold**
• Italic text: *italic*
• Links: [Text](https://www.sefaria.org/Reference)
• Lists: - item or 1. item
• Code blocks: ```code```
• Blockquotes: > quote"""
        else:
            # Use default system prompt
            return DEFAULT_SYSTEM_PROMPT
    
    @observe(name="claude-agent-send-message")
    async def send_message(
        self,
        messages: list[ConversationMessage],
        on_progress: Optional[Callable[[AgentProgressUpdate], None]] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> AgentResponse:
        """
        Send a message to the agent and get a response.
        
        Args:
            messages: Conversation history
            on_progress: Optional callback for progress updates
            session_id: Session ID for logging
            user_id: User ID for logging
            
        Returns:
            AgentResponse with content and metadata
        """
        start_time = time.time()
        
        # Get last user message for logging
        last_user_message = next(
            (m.content for m in reversed(messages) if m.role == 'user'),
            ''
        )
        
        # Use Langfuse propagate_attributes for trace context
        # Note: All metadata values must be strings for OpenTelemetry
        with propagate_attributes(
            user_id=user_id,
            session_id=session_id,
            tags=["sefaria-agent", "chatbot"],
            metadata={
                "model": self.model,
                "max_iterations": str(self.max_iterations),
            }
        ):
            # Update current trace with input
            if self.langfuse_enabled:
                try:
                    langfuse = get_client()
                    langfuse.update_current_trace(
                        input={"user_message": last_user_message[:2000]},
                    )
                except Exception as e:
                    logger.debug(f"Failed to update trace input: {e}")
            
            # Convert messages to Anthropic format
            conversation = [
                {
                    'role': m.role,
                    'content': [{'type': 'text', 'text': m.content}]
                }
                for m in messages
            ]
            
            def emit(update: AgentProgressUpdate):
                """Safely emit progress update."""
                if on_progress:
                    try:
                        on_progress(update)
                    except Exception as e:
                        logger.warning(f"Progress callback error: {e}")
            
            iterations = 0
            final_text = ''
            llm_calls = 0
            tool_calls_list = []
            input_tokens = 0
            output_tokens = 0
            cache_creation_tokens = 0
            cache_read_tokens = 0
            
            while iterations < self.max_iterations:
                iterations += 1
                llm_calls += 1
                
                emit(AgentProgressUpdate(
                    type='status',
                    text=f'Thinking (pass {iterations})…'
                ))
                
                # Call Claude with tracing
                response, usage = await self._call_claude(conversation, iterations)
                
                # Track token usage
                input_tokens += usage['input_tokens']
                output_tokens += usage['output_tokens']
                cache_creation_tokens += usage.get('cache_creation_tokens', 0)
                cache_read_tokens += usage.get('cache_read_tokens', 0)
                
                # Process response blocks
                blocks = response.content
                tool_uses = [b for b in blocks if b.type == 'tool_use']
                text_blocks = [b for b in blocks if b.type == 'text']
                text = ''.join(b.text for b in text_blocks)
                final_text += text
                
                # Add assistant response to conversation
                conversation.append({
                    'role': 'assistant',
                    'content': [self._block_to_dict(b) for b in blocks]
                })
                
                # If no tool uses, we're done
                if not tool_uses:
                    break
                
                # Execute tool calls
                for tool_use in tool_uses:
                    tool_name = tool_use.name
                    tool_input = tool_use.input or {}
                    tool_use_id = tool_use.id
                    
                    tool_desc = describe_tool_call(tool_name, tool_input)
                    
                    emit(AgentProgressUpdate(
                        type='tool_start',
                        tool_name=tool_name,
                        tool_input=tool_input,
                        description=tool_desc
                    ))
                    
                    # Execute the tool with tracing
                    tool_result = await self._execute_tool(
                        tool_name,
                        tool_input,
                        tool_use_id
                    )
                    
                    # Track tool call
                    tool_calls_list.append({
                        'tool_name': tool_name,
                        'tool_input': tool_input,
                        'tool_use_id': tool_use_id,
                        'is_error': tool_result.is_error
                    })
                    
                    # Get output preview
                    output_text = ''.join(
                        b.get('text', '') if b.get('type') == 'text' else json.dumps(b)
                        for b in tool_result.content
                    )
                    output_preview = output_text[:500] + ('…' if len(output_text) > 500 else '')
                    
                    emit(AgentProgressUpdate(
                        type='tool_end',
                        tool_name=tool_name,
                        is_error=tool_result.is_error,
                        output_preview=output_preview
                    ))
                    
                    # Add tool result to conversation
                    conversation.append({
                        'role': 'user',
                        'content': [{
                            'type': 'tool_result',
                            'tool_use_id': tool_use_id,
                            'content': tool_result.content,
                            'is_error': tool_result.is_error
                        }]
                    })
            
            emit(AgentProgressUpdate(
                type='status',
                text='Synthesizing response…'
            ))
            
            # Calculate latency
            latency_ms = int((time.time() - start_time) * 1000)
            
            # Final output
            output = final_text.strip()
            if not output and iterations >= self.max_iterations:
                output = 'Sorry, I hit a tool loop limit while processing your request.'
            elif not output:
                output = 'Sorry, I encountered an issue generating a response.'
            
            # Update current trace with output
            if self.langfuse_enabled:
                try:
                    langfuse = get_client()
                    langfuse.update_current_trace(
                        output={"response": output[:2000]},
                        metadata={
                            "iterations": iterations,
                            "llm_calls": llm_calls,
                            "tool_calls_count": len(tool_calls_list),
                            "tool_names": [tc['tool_name'] for tc in tool_calls_list],
                            "latency_ms": latency_ms,
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                        }
                    )
                except Exception as e:
                    logger.debug(f"Failed to update trace output: {e}")
            
            logger.info(
                f"Agent response: user={user_id} session={session_id} "
                f"iterations={iterations} tools={len(tool_calls_list)} latency={latency_ms}ms"
            )
            
            return AgentResponse(
                content=output,
                tool_calls=tool_calls_list,
                llm_calls=llm_calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_creation_tokens=cache_creation_tokens,
                cache_read_tokens=cache_read_tokens,
                latency_ms=latency_ms
            )
    
    @observe(as_type="generation", name="claude-completion")
    async def _call_claude(self, conversation: list, iteration: int) -> tuple:
        """Call Claude API with Langfuse generation tracking."""
        # Update observation with model info before the call
        if self.langfuse_enabled:
            try:
                langfuse = get_client()
                langfuse.update_current_observation(
                    model=self.model,
                    model_parameters={
                        "max_tokens": self.max_tokens,
                        "temperature": self.temperature,
                    },
                    input=conversation,
                    metadata={"iteration": str(iteration)},
                )
            except Exception as e:
                logger.debug(f"Failed to update generation input: {e}")
        
        # Log tool schemas being sent (for debugging)
        logger.debug(f"Sending {len(SEFARIA_TOOL_SCHEMAS)} tools to Claude")
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=self.system_prompt,
            messages=conversation,
            tools=SEFARIA_TOOL_SCHEMAS,
            tool_choice={'type': 'auto'}
        )
        
        # Log response info for debugging
        logger.debug(f"Claude response: stop_reason={response.stop_reason}, blocks={len(response.content)}")
        
        usage = {
            'input_tokens': response.usage.input_tokens,
            'output_tokens': response.usage.output_tokens,
            'cache_creation_tokens': getattr(response.usage, 'cache_creation_input_tokens', 0) or 0,
            'cache_read_tokens': getattr(response.usage, 'cache_read_input_tokens', 0) or 0,
        }
        
        # Update observation with usage and output
        if self.langfuse_enabled:
            try:
                langfuse = get_client()
                # Extract response content for logging
                output_content = []
                for block in response.content:
                    if block.type == 'text':
                        output_content.append({'type': 'text', 'text': block.text[:1000]})
                    elif block.type == 'tool_use':
                        output_content.append({
                            'type': 'tool_use',
                            'name': block.name,
                            'input': block.input
                        })
                
                langfuse.update_current_observation(
                    output=output_content,
                    usage={
                        "input": usage['input_tokens'],
                        "output": usage['output_tokens'],
                        "total": usage['input_tokens'] + usage['output_tokens'],
                    },
                    metadata={
                        "stop_reason": response.stop_reason,
                        "cache_creation_tokens": str(usage['cache_creation_tokens']),
                        "cache_read_tokens": str(usage['cache_read_tokens']),
                    }
                )
            except Exception as e:
                logger.debug(f"Failed to update generation output: {e}")
        
        return response, usage
    
    @observe(as_type="span", name="tool-execution")
    async def _execute_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_use_id: str
    ):
        """Execute a tool with Langfuse span tracking."""
        # Update span with tool input
        if self.langfuse_enabled:
            try:
                langfuse = get_client()
                langfuse.update_current_observation(
                    input={
                        "tool_name": tool_name,
                        "tool_input": tool_input,
                        "tool_use_id": tool_use_id,
                    },
                    metadata={
                        "tool_description": describe_tool_call(tool_name, tool_input),
                    }
                )
            except Exception as e:
                logger.debug(f"Failed to update tool input: {e}")
        
        start_time = time.time()
        result = await self.tool_executor.execute(tool_name, tool_input)
        execution_time_ms = int((time.time() - start_time) * 1000)
        
        # Update span with tool output
        if self.langfuse_enabled:
            try:
                langfuse = get_client()
                # Extract output text
                output_text = ''.join(
                    b.get('text', '')[:2000] if b.get('type') == 'text' else ''
                    for b in result.content
                )
                
                langfuse.update_current_observation(
                    output={
                        "result_preview": output_text[:1000],
                        "is_error": result.is_error,
                    },
                    metadata={
                        "execution_time_ms": str(execution_time_ms),
                        "is_error": str(result.is_error),
                    }
                )
            except Exception as e:
                logger.debug(f"Failed to update tool output: {e}")
        
        return result
    
    def _block_to_dict(self, block) -> dict[str, Any]:
        """Convert an Anthropic content block to a dictionary."""
        if block.type == 'text':
            return {'type': 'text', 'text': block.text}
        elif block.type == 'tool_use':
            return {
                'type': 'tool_use',
                'id': block.id,
                'name': block.name,
                'input': block.input
            }
        else:
            return {'type': block.type}
    
    def reload_prompt(self):
        """Reload the system prompt from Langfuse (useful for hot updates)."""
        self.system_prompt = self._build_system_prompt()
        logger.info(f"System prompt reloaded ({len(self.system_prompt)} chars)")
        return len(self.system_prompt)
    
    async def close(self):
        """Close the service and cleanup resources."""
        await self.sefaria_client.close()
        
        # Flush Langfuse events
        if self.langfuse_enabled:
            try:
                langfuse = get_client()
                langfuse.flush()
            except Exception as e:
                logger.debug(f"Failed to flush Langfuse: {e}")


# Convenience function for synchronous contexts
def create_agent_service(
    api_key: Optional[str] = None,
    **kwargs
) -> ClaudeAgentService:
    """Create a new agent service instance."""
    return ClaudeAgentService(api_key=api_key, **kwargs)
