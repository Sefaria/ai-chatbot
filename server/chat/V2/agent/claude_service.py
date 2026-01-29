"""
Claude Agent Service with Braintrust tracing.

This is the core agent runtime that:
- Loads the core system prompt from Braintrust
- Executes Claude with the full toolset
- Uses Braintrust native tracing (@traced decorator)
"""

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import anthropic
import braintrust
from braintrust import current_span, traced

from ..prompts import PromptService, get_prompt_service
from .sefaria_client import SefariaClient
from .tool_executor import SefariaToolExecutor, describe_tool_call
from .tool_schemas import get_all_tools

logger = logging.getLogger("chat.agent")


@dataclass
class AgentProgressUpdate:
    """Progress update from the agent."""

    type: str  # 'status', 'tool_start', 'tool_end', 'complete'
    text: str | None = None
    tool_name: str | None = None
    tool_input: dict | None = None
    description: str | None = None
    is_error: bool | None = None
    output_preview: str | None = None


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
    trace_id: str | None = None


def truncate(text: str, max_len: int) -> str:
    """Truncate text to max length."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def extract_refs(tool_calls: list) -> list:
    """Extract unique Sefaria refs from tool calls for Braintrust logging."""
    seen = set()
    refs = []
    for tc in tool_calls:
        ref = tc.get("tool_input", {}).get("reference")
        if ref and ref not in seen:
            seen.add(ref)
            refs.append(ref)
    return refs


class ClaudeAgentService:
    """
    Claude agent service with the core prompt and full toolset.

    Integrates:
    - Core prompt loading from Braintrust
    - Braintrust native tracing (@traced decorator)
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-5-20250929",
        max_iterations: int = 10,
        max_tokens: int = 8000,
        temperature: float = 0.7,
        prompt_service: PromptService | None = None,
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
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY is required")

        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.model = model
        self.max_iterations = max_iterations
        self.max_tokens = max_tokens
        self.temperature = temperature

        self.sefaria_client = SefariaClient()
        self.tool_executor = SefariaToolExecutor(self.sefaria_client)
        self.prompt_service = prompt_service or get_prompt_service()

        bt_api_key = os.environ.get("BRAINTRUST_API_KEY")
        bt_project = os.environ.get("BRAINTRUST_PROJECT", "On Site Agent")
        if bt_api_key:
            try:
                self.bt_logger = braintrust.init_logger(
                    project=bt_project,
                    api_key=bt_api_key,
                )
                logger.info(f"✅ Braintrust tracing initialized for project: {bt_project}")
            except Exception as exc:
                logger.warning(f"⚠️  Failed to initialize Braintrust tracing: {exc}")
                self.bt_logger = None
        else:
            logger.warning("⚠️  BRAINTRUST_API_KEY not set, tracing disabled")
            self.bt_logger = None

        logger.info(f"ClaudeAgentService initialized with model: {model}")

    @traced(name="chat-agent", type="llm")
    async def send_message(
        self,
        messages: list[ConversationMessage],
        core_prompt_id: str | None = None,
        on_progress: Callable[[AgentProgressUpdate], None] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        turn_id: str | None = None,
        summary_text: str | None = None,
        summary_metadata: dict[str, Any] | None = None,
        client_version: str = "",
    ) -> AgentResponse:
        """
        Send a message to the agent using the core prompt and full toolset.

        Args:
            messages: Conversation history
            core_prompt_id: Braintrust slug for core prompt (default: settings.CORE_PROMPT_SLUG)
            on_progress: Optional callback for progress updates
            session_id: Session ID for logging
            user_id: User ID for logging
            turn_id: Turn ID for logging
            summary_text: Optional summary text to include in the system prompt
            summary_metadata: Optional structured summary metadata for tracing

        Returns:
            AgentResponse with content and metadata
        """
        start_time = time.time()
        span = current_span()
        trace_id = getattr(span, "id", None)

        last_user_message = next((m.content for m in reversed(messages) if m.role == "user"), "")

        def emit(update: AgentProgressUpdate) -> None:
            if on_progress:
                try:
                    on_progress(update)
                except Exception as exc:
                    logger.warning(f"Progress callback error: {exc}")

        core_prompt = self.prompt_service.get_core_prompt(prompt_id=core_prompt_id)

        system_prompt = core_prompt.text
        if summary_text:
            system_prompt = f"{system_prompt}\n\nConversation summary:\n{summary_text}"

        tools = get_all_tools()
        tool_names = [t["name"] for t in tools]
        logger.info(f"Tools loaded: {len(tools)} tools ({tool_names})")

        conversation = [
            {"role": m.role, "content": [{"type": "text", "text": m.content}]}
            for m in messages
        ]

        iterations = 0
        final_text = ""
        llm_calls = 0
        tool_calls_list = []
        input_tokens = 0
        output_tokens = 0
        cache_creation_tokens = 0
        cache_read_tokens = 0

        formatted_messages = [
            {"role": "system", "content": system_prompt},
            *[{"role": m.role, "content": m.content} for m in messages],
        ]
        environment = os.environ.get("ENVIRONMENT", "dev")

        input_payload = {
            "query": last_user_message,
            "messages": formatted_messages,
        }
        metadata = {
            "session_id": session_id or "",
            "turn_id": turn_id or "",
            "user_id": user_id or "",
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "core_prompt_id": core_prompt.prompt_id,
            "core_prompt_version": core_prompt.version,
            "tools_available": tool_names,
            "client_version": client_version,
            "input_json": input_payload,
        }
        if summary_text:
            metadata["conversation_summary"] = summary_text
        if summary_metadata:
            metadata["conversation_summary_structured"] = summary_metadata

        span.log(
            input=last_user_message,
            tags=["core", environment],
            metadata=metadata,
        )

        while iterations < self.max_iterations:
            iterations += 1
            llm_calls += 1

            emit(AgentProgressUpdate(type="status", text=f"Thinking (pass {iterations})…"))

            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                system=system_prompt,
                messages=conversation,
                tools=tools,
                tool_choice={"type": "auto"},
            )

            usage = response.usage
            input_tokens += usage.input_tokens
            output_tokens += usage.output_tokens
            cache_creation_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
            cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0

            blocks = response.content
            tool_uses = [b for b in blocks if b.type == "tool_use"]
            text_blocks = [b for b in blocks if b.type == "text"]
            text = "".join(b.text for b in text_blocks)
            final_text += text

            conversation.append(
                {"role": "assistant", "content": [self._block_to_dict(b) for b in blocks]}
            )

            if not tool_uses:
                if iterations == 1:
                    logger.warning(
                        "Claude did not use any tools on first iteration. "
                        f"Tools available: {len(tools)}"
                    )
                break

            for tool_use in tool_uses:
                tool_name = tool_use.name
                tool_input = tool_use.input or {}
                tool_use_id = tool_use.id

                tool_desc = describe_tool_call(tool_name, tool_input)

                emit(
                    AgentProgressUpdate(
                        type="tool_start",
                        tool_name=tool_name,
                        tool_input=tool_input,
                        description=tool_desc,
                    )
                )

                @traced(name=f"tool:{tool_name}", type="tool")
                async def execute_tool_traced():
                    tool_span = current_span()
                    tool_start = time.time()

                    tool_span.log(
                        input=json.dumps(tool_input),
                        metadata={
                            "tool_name": tool_name,
                            "tool_use_id": tool_use_id,
                        },
                    )

                    result = await self.tool_executor.execute(tool_name, tool_input)
                    tool_latency = int((time.time() - tool_start) * 1000)

                    output_text = "".join(
                        b.get("text", "") if b.get("type") == "text" else json.dumps(b)
                        for b in result.content
                    )
                    output_preview = truncate(output_text, 500)

                    tool_span.log(
                        output=output_preview,
                        **({"error": output_preview} if result.is_error else {}),
                        metadata={
                            "tool_name": tool_name,
                            "tool_use_id": tool_use_id,
                            "is_error": result.is_error,
                        },
                        metrics={
                            "latency_ms": tool_latency,
                        },
                    )

                    return result, output_preview, tool_latency

                result, output_preview, tool_latency = await execute_tool_traced()

                tool_call_data = {
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "tool_use_id": tool_use_id,
                    "tool_output": output_preview,
                    "is_error": result.is_error,
                    "latency_ms": tool_latency,
                }
                tool_calls_list.append(tool_call_data)

                emit(
                    AgentProgressUpdate(
                        type="tool_end",
                        tool_name=tool_name,
                        is_error=result.is_error,
                        output_preview=output_preview,
                    )
                )

                conversation.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": result.content,
                                "is_error": result.is_error,
                            }
                        ],
                    }
                )

        emit(AgentProgressUpdate(type="status", text="Synthesizing response…"))

        latency_ms = int((time.time() - start_time) * 1000)

        output = final_text.strip()
        if not output and iterations >= self.max_iterations:

            def build_tool_limit_response(tool_calls: list[dict[str, Any]]) -> str:
                caveat = (
                    "I hit the tool-use limit before I could finish. "
                    "Here is what I have so far:"
                )
                if not tool_calls:
                    return caveat

                lines = [caveat]
                for tc in tool_calls:
                    output_preview = (tc.get("tool_output") or "").strip()
                    if not output_preview:
                        continue
                    tool_name = tc.get("tool_name", "tool")
                    error_suffix = " (error)" if tc.get("is_error") else ""
                    lines.append(f"- {tool_name}{error_suffix}: {output_preview}")

                if len(lines) == 1:
                    lines.append("- No tool output was returned.")

                return "\n".join(lines)

            output = build_tool_limit_response(tool_calls_list)
        elif not output:
            output = "Sorry, I encountered an issue generating a response."

        def summarize_tool_call(tc: dict) -> dict:
            summary = {
                "name": tc["tool_name"],
                "input": tc.get("tool_input", {}),
                "output_preview": tc.get("tool_output", ""),
                "is_error": tc.get("is_error", False),
            }
            if tc.get("is_error"):
                summary["output_full"] = tc.get("tool_output", "")
            return summary

        tool_calls_summary = [summarize_tool_call(tc) for tc in tool_calls_list]

        output_payload = {
            "response": output,
            "refs": extract_refs(tool_calls_list),
            "tool_calls": tool_calls_summary,
        }
        span.log(
            output=output,
            metadata={
                "tools_used": [tc["tool_name"] for tc in tool_calls_list],
                "output_json": output_payload,
            },
            metrics={
                "latency_ms": latency_ms,
                "llm_calls": llm_calls,
                "tool_calls": len(tool_calls_list),
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "cache_creation_input_tokens": cache_creation_tokens,
                "cache_read_input_tokens": cache_read_tokens,
                "total_tokens": input_tokens + output_tokens + cache_creation_tokens,
            },
        )

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
            latency_ms=latency_ms,
            trace_id=trace_id,
        )

    def _block_to_dict(self, block: Any) -> dict[str, Any]:
        """Convert an Anthropic content block to a dictionary."""
        if block.type == "text":
            return {"type": "text", "text": block.text}
        if block.type == "tool_use":
            return {
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            }
        return {"type": block.type}

    async def close(self) -> None:
        """Close the service and cleanup resources."""
        await self.sefaria_client.close()


# Convenience function
def get_agent_service() -> ClaudeAgentService:
    """Get a singleton agent service instance."""
    return ClaudeAgentService()
