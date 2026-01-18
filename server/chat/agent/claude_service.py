"""
Claude Agent Service with routed flow support and Braintrust native tracing.

This is the core agent runtime that:
- Receives routing decisions from the router
- Loads appropriate prompts from Braintrust
- Executes Claude with flow-specific tools
- Uses Braintrust native tracing (@traced decorator)
"""

import os
import json
import time
import logging
from typing import Any, Optional, Callable, List, Dict
from dataclasses import dataclass

import anthropic
import braintrust
from braintrust import Span, traced, current_span

from .tool_schemas import get_tools_by_names
from .tool_executor import SefariaToolExecutor, describe_tool_call
from .sefaria_client import SefariaClient

from ..router import RouteResult, Flow
from ..prompts import PromptService, get_prompt_service

logger = logging.getLogger('chat.agent')


@dataclass
class AgentProgressUpdate:
    """Progress update from the agent."""
    type: str  # 'status', 'tool_start', 'tool_end', 'routing', 'complete'
    text: Optional[str] = None
    tool_name: Optional[str] = None
    tool_input: Optional[dict] = None
    description: Optional[str] = None
    is_error: Optional[bool] = None
    output_preview: Optional[str] = None
    flow: Optional[str] = None


@dataclass
class ConversationMessage:
    """A message in the conversation."""
    role: str  # 'user' or 'assistant'
    content: str


@dataclass
class AgentResponse:
    """Response from the agent including metadata."""
    content: str
    tool_calls: List[Dict[str, Any]]
    llm_calls: int
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    latency_ms: int
    flow: str = ""
    decision_id: str = ""
    was_refused: bool = False


def truncate(text: str, max_len: int) -> str:
    """Truncate text to max length."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 1] + '…'


class ClaudeAgentService:
    """
    Claude agent service with routed flow support.

    Integrates:
    - Flow-specific prompt loading from Braintrust
    - Flow-specific tool selection
    - Braintrust native tracing (@traced decorator)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = 'claude-sonnet-4-5-20250929',
        max_iterations: int = 10,
        max_tokens: int = 8000,
        temperature: float = 0.7,
        prompt_service: Optional[PromptService] = None,
    ):
        """
        Initialize the Claude agent service.

        Args:
            api_key: Anthropic API key (default: from env)
            model: Claude model to use
            max_iterations: Maximum tool-use iterations
            max_tokens: Maximum response tokens
            temperature: Sampling temperature
            prompt_service: Braintrust prompt service
        """
        self.api_key = api_key or os.environ.get('ANTHROPIC_API_KEY')
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY is required")

        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.model = model
        self.max_iterations = max_iterations
        self.max_tokens = max_tokens
        self.temperature = temperature

        # Sefaria tools
        self.sefaria_client = SefariaClient()
        self.tool_executor = SefariaToolExecutor(self.sefaria_client)

        # Services
        self.prompt_service = prompt_service or get_prompt_service()

        # Initialize Braintrust logger for tracing
        bt_api_key = os.environ.get('BRAINTRUST_API_KEY')
        bt_project = os.environ.get('BRAINTRUST_PROJECT', 'On Site Agent')
        if bt_api_key:
            try:
                self.bt_logger = braintrust.init_logger(
                    project=bt_project,
                    api_key=bt_api_key,
                )
                logger.info(f"✅ Braintrust tracing initialized for project: {bt_project}")
            except Exception as e:
                logger.warning(f"⚠️  Failed to initialize Braintrust tracing: {e}")
                self.bt_logger = None
        else:
            logger.warning("⚠️  BRAINTRUST_API_KEY not set, tracing disabled")
            self.bt_logger = None

        logger.info(f"ClaudeAgentService initialized with model: {model}")

    @traced(name="chat-agent", type="llm")
    async def send_message(
        self,
        messages: List[ConversationMessage],
        route_result: RouteResult,
        on_progress: Optional[Callable[[AgentProgressUpdate], None]] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        turn_id: Optional[str] = None,
    ) -> AgentResponse:
        """
        Send a message to the agent with routing context.
        Uses Braintrust native tracing.

        Args:
            messages: Conversation history
            route_result: Routing decision from the router
            on_progress: Optional callback for progress updates
            session_id: Session ID for logging
            user_id: User ID for logging
            turn_id: Turn ID for logging

        Returns:
            AgentResponse with content and metadata
        """
        start_time = time.time()
        span = current_span()

        # Get last user message - needed for both refusal and normal logging
        last_user_message = next(
            (m.content for m in reversed(messages) if m.role == 'user'),
            ''
        )

        # Handle refusal flow
        if route_result.flow == Flow.REFUSE:
            return self._create_refusal_response(route_result, start_time)

        def emit(update: AgentProgressUpdate):
            """Safely emit progress update."""
            if on_progress:
                try:
                    on_progress(update)
                except Exception as e:
                    logger.warning(f"Progress callback error: {e}")

        # Emit routing info
        emit(AgentProgressUpdate(
            type='routing',
            flow=route_result.flow.value,
            text=f'Routing to {route_result.flow.value} flow',
        ))

        # Load prompts for this flow
        prompt_bundle = self.prompt_service.get_prompt_bundle(
            flow=route_result.flow.value,
            core_prompt_id=route_result.prompt_bundle.core_prompt_id,
            flow_prompt_id=route_result.prompt_bundle.flow_prompt_id,
        )

        # Get tools for this flow
        tools = get_tools_by_names(route_result.tools)

        # Log tools being passed to Claude
        tool_names = [t['name'] for t in tools]
        logger.info(
            f"Tools for flow {route_result.flow.value}: "
            f"{len(tools)} tools loaded ({tool_names})"
        )

        if not tools:
            logger.warning(
                f"No tools loaded for flow {route_result.flow.value}! "
                f"Requested tools: {route_result.tools}"
            )

        # Convert messages to Anthropic format
        conversation = [
            {
                'role': m.role,
                'content': [{'type': 'text', 'text': m.content}]
            }
            for m in messages
        ]

        # Initialize counters
        iterations = 0
        final_text = ''
        llm_calls = 0
        tool_calls_list = []
        input_tokens = 0
        output_tokens = 0
        cache_creation_tokens = 0
        cache_read_tokens = 0

        # Log initial input to span
        span.log(
            input=truncate(last_user_message, 2000),
            metadata={
                'model': self.model,
                'flow': route_result.flow.value,
                'session_id': session_id or '',
                'user_id': user_id or '',
                'turn_id': turn_id or '',
                'decision_id': route_result.decision_id,
                'core_prompt_id': prompt_bundle.core_prompt_id,
                'core_prompt_version': prompt_bundle.core_prompt_version,
                'flow_prompt_id': prompt_bundle.flow_prompt_id,
                'flow_prompt_version': prompt_bundle.flow_prompt_version,
                'tools_available': route_result.tools,
            }
        )

        # Agent loop
        while iterations < self.max_iterations:
            iterations += 1
            llm_calls += 1

            emit(AgentProgressUpdate(
                type='status',
                text=f'Thinking (pass {iterations})…',
                flow=route_result.flow.value,
            ))

            # Call Claude
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                system=prompt_bundle.system_prompt,
                messages=conversation,
                tools=tools,
                tool_choice={'type': 'auto'}
            )

            # Track token usage
            usage = response.usage
            input_tokens += usage.input_tokens
            output_tokens += usage.output_tokens
            cache_creation_tokens += getattr(usage, 'cache_creation_input_tokens', 0) or 0
            cache_read_tokens += getattr(usage, 'cache_read_input_tokens', 0) or 0

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
                if iterations == 1:
                    logger.warning(
                        f"Claude did not use any tools on first iteration. "
                        f"Flow: {route_result.flow.value}, Tools available: {len(tools)}"
                    )
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
                    description=tool_desc,
                    flow=route_result.flow.value,
                ))

                # Execute tool with nested span
                @traced(name=f"tool:{tool_name}", type="tool")
                async def execute_tool_traced():
                    tool_span = current_span()
                    tool_start = time.time()

                    # Log tool input
                    tool_span.log(
                        input=json.dumps(tool_input),
                        metadata={
                            'tool_name': tool_name,
                            'tool_use_id': tool_use_id,
                        }
                    )

                    # Execute the tool
                    result = await self.tool_executor.execute(tool_name, tool_input)
                    tool_latency = int((time.time() - tool_start) * 1000)

                    # Get output preview
                    output_text = ''.join(
                        b.get('text', '') if b.get('type') == 'text' else json.dumps(b)
                        for b in result.content
                    )
                    output_preview = truncate(output_text, 500)

                    # Log tool output
                    tool_span.log(
                        output=output_preview,
                        **({"error": output_preview} if result.is_error else {}),
                        metadata={
                            'tool_name': tool_name,
                            'tool_use_id': tool_use_id,
                            'is_error': result.is_error,
                        },
                        metrics={
                            'latency_ms': tool_latency,
                        }
                    )

                    return result, output_preview, tool_latency

                result, output_preview, tool_latency = await execute_tool_traced()

                # Track tool call
                tool_call_data = {
                    'tool_name': tool_name,
                    'tool_input': tool_input,
                    'tool_use_id': tool_use_id,
                    'is_error': result.is_error,
                    'latency_ms': tool_latency,
                }
                tool_calls_list.append(tool_call_data)

                emit(AgentProgressUpdate(
                    type='tool_end',
                    tool_name=tool_name,
                    is_error=result.is_error,
                    output_preview=output_preview,
                    flow=route_result.flow.value,
                ))

                # Add tool result to conversation
                conversation.append({
                    'role': 'user',
                    'content': [{
                        'type': 'tool_result',
                        'tool_use_id': tool_use_id,
                        'content': result.content,
                        'is_error': result.is_error
                    }]
                })

        emit(AgentProgressUpdate(
            type='status',
            text='Synthesizing response…',
            flow=route_result.flow.value,
        ))

        # Calculate latency
        latency_ms = int((time.time() - start_time) * 1000)

        # Final output
        output = final_text.strip()
        if not output and iterations >= self.max_iterations:
            output = 'Sorry, I hit a tool loop limit while processing your request.'
        elif not output:
            output = 'Sorry, I encountered an issue generating a response.'

        # Log final output and metrics to span
        span.log(
            output=output,
            metadata={
                'outputLength': len(output),
                'toolNames': [tc['tool_name'] for tc in tool_calls_list],
            },
            metrics={
                'llm_calls': llm_calls,
                'tool_calls': len(tool_calls_list),
                'prompt_tokens': input_tokens,
                'completion_tokens': output_tokens,
                'cache_creation_input_tokens': cache_creation_tokens,
                'cache_read_input_tokens': cache_read_tokens,
                'total_tokens': input_tokens + output_tokens + cache_creation_tokens,
                'latency_ms': latency_ms,
            }
        )

        logger.info(
            f"Agent response: user={user_id} session={session_id} flow={route_result.flow.value} "
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
            latency_ms=latency_ms,
            flow=route_result.flow.value,
            decision_id=route_result.decision_id,
        )

    def _create_refusal_response(
        self,
        route_result: RouteResult,
        start_time: float,
    ) -> AgentResponse:
        """Create a response for refused requests."""
        latency_ms = int((time.time() - start_time) * 1000)

        refusal_message = route_result.safety.refusal_message or \
            "I'm not able to help with that request. Let me know if there's something else I can help you with."

        logger.warning(f"Request refused: {route_result.safety.refusal_message}")

        return AgentResponse(
            content=refusal_message,
            tool_calls=[],
            llm_calls=0,
            input_tokens=0,
            output_tokens=0,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            latency_ms=latency_ms,
            flow=route_result.flow.value,
            decision_id=route_result.decision_id,
            was_refused=True,
        )

    def _block_to_dict(self, block: Any) -> Dict[str, Any]:
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

    async def close(self):
        """Close the service and cleanup resources."""
        await self.sefaria_client.close()


# Convenience function
def get_agent_service() -> ClaudeAgentService:
    """Get a singleton agent service instance."""
    # Note: In production, you might want to cache this
    return ClaudeAgentService()
