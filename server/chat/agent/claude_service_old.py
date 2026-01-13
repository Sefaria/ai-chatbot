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
import uuid
from typing import Any, Optional, Callable, List, Dict
from dataclasses import dataclass

import anthropic
import braintrust
from braintrust import Span, traced, current_span

from .tool_schemas import get_tools_for_flow, get_tools_by_names, ALL_TOOLS
from .tool_executor import SefariaToolExecutor, describe_tool_call
from .sefaria_client import SefariaClient

from ..router import RouteResult, Flow
from ..prompts import PromptService, PromptBundle, get_prompt_service

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
        turn_id = turn_id or f"turn_{uuid.uuid4().hex[:16]}"
        
        # Handle refusal flow
        if route_result.flow == Flow.REFUSE:
            return self._create_refusal_response(
                route_result,
                start_time,
            )
        
        # Get last user message for logging
        last_user_message = next(
            (m.content for m in reversed(messages) if m.role == 'user'),
            ''
        )
        
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
        
        # Create trace context
        trace_ctx = self.tracer.create_trace(
            session_id=session_id or "",
            turn_id=turn_id,
            user_id=user_id,
            flow=route_result.flow.value,
            decision_id=route_result.decision_id,
            metadata={
                "model": self.model,
                "max_iterations": self.max_iterations,
            },
            tags=["sefaria-agent", route_result.flow.value.lower()],
        )
        
        # Create run log
        run_log = self.bt_logger.create_run_log(
            session_id=session_id or "",
            turn_id=turn_id,
            decision_id=route_result.decision_id,
            user_message=last_user_message,
            flow=route_result.flow.value,
        )
        
        try:
            # Load prompts for this flow
            prompt_start = time.time()
            prompt_bundle = self.prompt_service.get_prompt_bundle(
                flow=route_result.flow.value,
                core_prompt_id=route_result.prompt_bundle.core_prompt_id,
                flow_prompt_id=route_result.prompt_bundle.flow_prompt_id,
            )
            prompt_latency = int((time.time() - prompt_start) * 1000)
            
            # Update run log with prompt info
            run_log.core_prompt_id = prompt_bundle.core_prompt_id
            run_log.core_prompt_version = prompt_bundle.core_prompt_version
            run_log.flow_prompt_id = prompt_bundle.flow_prompt_id
            run_log.flow_prompt_version = prompt_bundle.flow_prompt_version
            
            # Log prompt fetch
            self.tracer.log_prompt_fetch(
                trace_ctx,
                prompt_ids={
                    "core": prompt_bundle.core_prompt_id,
                    "flow": prompt_bundle.flow_prompt_id,
                },
                prompt_versions={
                    "core": prompt_bundle.core_prompt_version,
                    "flow": prompt_bundle.flow_prompt_version,
                },
                latency_ms=prompt_latency,
            )
            
            # Get tools for this flow
            tools = get_tools_by_names(route_result.tools)
            run_log.tools_available = route_result.tools
            
            # Convert messages to Anthropic format
            conversation = [
                {
                    'role': m.role,
                    'content': [{'type': 'text', 'text': m.content}]
                }
                for m in messages
            ]
            
            # Run agent loop
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
                    text=f'Thinking (pass {iterations})…',
                    flow=route_result.flow.value,
                ))
                
                # Call Claude
                llm_start = time.time()
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    system=prompt_bundle.system_prompt,
                    messages=conversation,
                    tools=tools,
                    tool_choice={'type': 'auto'}
                )
                llm_latency = int((time.time() - llm_start) * 1000)
                
                # Track token usage
                usage = {
                    'input_tokens': response.usage.input_tokens,
                    'output_tokens': response.usage.output_tokens,
                    'cache_creation_tokens': getattr(response.usage, 'cache_creation_input_tokens', 0) or 0,
                    'cache_read_tokens': getattr(response.usage, 'cache_read_input_tokens', 0) or 0,
                }
                input_tokens += usage['input_tokens']
                output_tokens += usage['output_tokens']
                cache_creation_tokens += usage['cache_creation_tokens']
                cache_read_tokens += usage['cache_read_tokens']
                
                # Log LLM call
                self.tracer.log_llm_call(
                    trace_ctx,
                    model=self.model,
                    messages=conversation,
                    response_content=self._extract_text_from_response(response),
                    usage=usage,
                    latency_ms=llm_latency,
                    iteration=iterations,
                )
                
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
                        description=tool_desc,
                        flow=route_result.flow.value,
                    ))
                    
                    # Execute the tool
                    tool_start = time.time()
                    result = await self.tool_executor.execute(tool_name, tool_input)
                    tool_latency = int((time.time() - tool_start) * 1000)
                    
                    # Get output preview
                    output_text = ''.join(
                        b.get('text', '') if b.get('type') == 'text' else json.dumps(b)
                        for b in result.content
                    )
                    output_preview = output_text[:500] + ('…' if len(output_text) > 500 else '')
                    
                    # Track tool call
                    tool_call_data = {
                        'tool_name': tool_name,
                        'tool_input': tool_input,
                        'tool_use_id': tool_use_id,
                        'is_error': result.is_error,
                        'latency_ms': tool_latency,
                    }
                    tool_calls_list.append(tool_call_data)
                    
                    # Log to tracer
                    self.tracer.log_tool_call(
                        trace_ctx,
                        tool_name=tool_name,
                        tool_input=tool_input,
                        tool_output=output_preview,
                        latency_ms=tool_latency,
                        is_error=result.is_error,
                    )
                    
                    # Log to Braintrust
                    event_id = f"evt_{uuid.uuid4().hex[:16]}"
                    self.bt_logger.log_tool_event(
                        run_log,
                        event_id=event_id,
                        tool_name=tool_name,
                        tool_input=tool_input,
                        tool_output=output_preview,
                        latency_ms=tool_latency,
                        success=not result.is_error,
                    )
                    
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
            
            # Finalize run log
            run_log.llm_calls = llm_calls
            run_log.input_tokens = input_tokens
            run_log.output_tokens = output_tokens
            run_log.finalize(output)
            
            # Log to Braintrust
            self.bt_logger.log_run(run_log)
            
            # End trace
            self.tracer.end_trace(
                trace_ctx,
                output=output,
                metrics={
                    "latency_ms": latency_ms,
                    "llm_calls": llm_calls,
                    "tool_calls": len(tool_calls_list),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
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
            
        except Exception as e:
            logger.error(f"Agent error: {e}", exc_info=True)
            
            # End trace with error
            self.tracer.end_trace(
                trace_ctx,
                output="",
                error=str(e),
            )
            
            # Log failed run
            run_log.finalize("Error occurred", was_refused=False)
            self.bt_logger.log_run(run_log)
            
            raise
    
    def _create_refusal_response(
        self,
        route_result: RouteResult,
        start_time: float,
    ) -> AgentResponse:
        """Create a response for refused requests."""
        latency_ms = int((time.time() - start_time) * 1000)
        
        refusal_message = route_result.safety.refusal_message or \
            "I'm not able to help with that request. Let me know if there's something else I can help you with."
        
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
    
    def _extract_text_from_response(self, response) -> str:
        """Extract text content from Claude response."""
        texts = []
        for block in response.content:
            if block.type == 'text':
                texts.append(block.text)
        return ''.join(texts)
    
    def _block_to_dict(self, block) -> Dict[str, Any]:
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
        self.bt_logger.flush()


# Convenience function
def create_agent_service(
    api_key: Optional[str] = None,
    **kwargs
) -> ClaudeAgentService:
    """Create a new agent service instance."""
    return ClaudeAgentService(api_key=api_key, **kwargs)
