"""
Claude Agent Service using the Claude Agent SDK with Braintrust tracing.

This service:
- Loads the core system prompt from Braintrust
- Uses the Claude Agent SDK for tool calling
- Emits progress updates during tool execution
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

try:
    import braintrust
    from braintrust import current_span
    from braintrust.wrappers.claude_agent_sdk import setup_claude_agent_sdk
except Exception:  # pragma: no cover - optional dependency
    braintrust = None
    setup_claude_agent_sdk = None
    current_span = None

# Global flag to ensure setup_claude_agent_sdk is only called once per process.
# IMPORTANT: This must be a global (not thread-local) because setup_claude_agent_sdk
# patches the SDK classes globally. Using thread-local would cause the SDK to be
# wrapped multiple times (once per thread), creating deeply nested spans.
_BRAINTRUST_SETUP_DONE = False

try:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, create_sdk_mcp_server, tool
except Exception:  # pragma: no cover - optional dependency
    ClaudeAgentOptions = None
    ClaudeSDKClient = None
    create_sdk_mcp_server = None
    tool = None

from ..prompts import PromptService, get_prompt_service
from ..prompts.prompt_fragments import build_system_prompt
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
class MessageContext:
    """Contextual information for a message, separate from the conversation itself."""

    summary_text: str | None = None
    page_url: str | None = None
    session_id: str | None = None


@dataclass
class AgentResponse:
    """Response from the agent including metadata."""

    content: str
    tool_calls: list[dict[str, Any]]
    llm_calls: int
    latency_ms: int
    model: str | None = None
    trace_id: str | None = None


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


def truncate(text: str, max_len: int) -> str:
    """Truncate text to max length."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


class ClaudeAgentService:
    """
    Claude agent service with the core prompt and full toolset.

    Uses the Claude Agent SDK with Braintrust tracing.
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
            max_iterations: Maximum tool-use iterations (handled internally by SDK)
            max_tokens: Maximum response tokens
            temperature: Sampling temperature
            prompt_service: Braintrust prompt service
        """
        if (
            ClaudeAgentOptions is None
            or ClaudeSDKClient is None
            or create_sdk_mcp_server is None
            or tool is None
        ):
            raise RuntimeError(
                "claude-agent-sdk is required. Install with `pip install claude-agent-sdk`."
            )

        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY is required")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            os.environ["ANTHROPIC_API_KEY"] = self.api_key

        self.model = model
        self.max_iterations = max_iterations
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.prompt_service = prompt_service or get_prompt_service()
        self._braintrust_api_key: str | None = None
        self._braintrust_project: str | None = None
        self._braintrust_enabled = False

        self.sefaria_client = SefariaClient()
        self.tool_executor = SefariaToolExecutor(self.sefaria_client)

        self._mcp_server_name = "sefaria"

        self._setup_braintrust_tracing()

    def _setup_braintrust_tracing(self) -> None:
        bt_api_key = os.environ.get("BRAINTRUST_API_KEY")
        bt_project = os.environ.get("BRAINTRUST_PROJECT", "On Site Agent")
        self._braintrust_api_key = bt_api_key
        self._braintrust_project = bt_project
        self._braintrust_enabled = False
        if not bt_api_key:
            return
        if setup_claude_agent_sdk is None:
            logger.warning("Braintrust Claude Agent SDK wrapper not available")
            return

        global _BRAINTRUST_SETUP_DONE
        if _BRAINTRUST_SETUP_DONE:
            self._braintrust_enabled = True
            return

        try:
            setup_claude_agent_sdk(project=bt_project, api_key=bt_api_key)
            self._braintrust_enabled = True
            _BRAINTRUST_SETUP_DONE = True
        except Exception as exc:
            logger.warning(f"Failed to setup Braintrust Claude Agent SDK: {exc}")

    async def send_message(
        self,
        messages: list[ConversationMessage],
        core_prompt_id: str | None = None,
        on_progress: Callable[[AgentProgressUpdate], None] | None = None,
        context: MessageContext | None = None,
    ) -> AgentResponse:
        """
        Send a message to the agent using the core prompt and full toolset.

        Args:
            messages: Conversation history
            core_prompt_id: Braintrust slug for core prompt (default: settings.CORE_PROMPT_SLUG)
            on_progress: Optional callback for progress updates
            context: Optional message context (page URL, summary, etc.)

        Returns:
            AgentResponse with content and metadata
        """
        self._setup_braintrust_tracing()
        context = context or MessageContext()

        async def run() -> AgentResponse:
            return await self._send_message_inner(
                messages=messages,
                core_prompt_id=core_prompt_id,
                on_progress=on_progress,
                context=context,
            )

        if braintrust and self._braintrust_enabled and hasattr(braintrust, "traced"):
            traced_run = braintrust.traced(name="chat-agent", type="llm")(run)
            return await traced_run()

        return await run()

    async def _send_message_inner(
        self,
        *,
        messages: list[ConversationMessage],
        core_prompt_id: str | None,
        on_progress: Callable[[AgentProgressUpdate], None] | None,
        context: MessageContext,
    ) -> AgentResponse:
        start_time = time.time()

        def emit(update: AgentProgressUpdate) -> None:
            if not on_progress:
                return
            try:
                on_progress(update)
            except Exception as exc:
                logger.warning(f"Progress callback error: {exc}")

        last_user_message = next(
            (message.content for message in reversed(messages) if message.role == "user"),
            "",
        )
        core_prompt = self.prompt_service.get_core_prompt(prompt_id=core_prompt_id)
        system_prompt, summary_included = build_system_prompt(
            core_prompt.text,
            summary_text=context.summary_text,
            page_url=context.page_url,
        )

        tool_calls_list: list[dict[str, Any]] = []
        tools = get_all_tools()
        sdk_tools = self._build_sdk_tools(tools, emit, tool_calls_list)

        allowed_tools = [
            f"mcp__{self._mcp_server_name}__{tool_schema['name']}" for tool_schema in tools
        ]
        mcp_server = create_sdk_mcp_server(
            name=self._mcp_server_name,
            version="1.0.0",
            tools=sdk_tools,
        )

        options, system_prompt_in_options = self._build_agent_options(
            system_prompt=system_prompt,
            mcp_server=mcp_server,
            allowed_tools=allowed_tools,
        )
        if current_span is not None:
            span = current_span()
            if span is not None:
                metadata = {
                    "core_prompt_id": core_prompt.prompt_id,
                    "core_prompt_version": core_prompt.version,
                    "core_prompt_in_options": system_prompt_in_options,
                    "summary_included": summary_included,
                    "model": self.model,
                }
                if context.session_id:
                    metadata["session_id"] = context.session_id
                if context.summary_text:
                    metadata["conversation_summary"] = context.summary_text
                span_input = {"message": last_user_message}
                if context.page_url:
                    span_input["page_url"] = context.page_url
                span.log(input=span_input, metadata=metadata)

        prompt_text = self._format_conversation(messages)
        if not system_prompt_in_options:
            prompt_text = f"{system_prompt}\n\n{prompt_text}" if prompt_text else system_prompt

        emit(AgentProgressUpdate(type="status", text="Thinking..."))

        try:
            final_text = ""
            trace_id = None
            async with ClaudeSDKClient(options=options) as client:
                await client.query(prompt_text)
                async for message in client.receive_response():
                    chunk = self._extract_text_from_message(message)
                    if chunk:
                        final_text += chunk
                trace_id = getattr(client, "trace_id", None) or getattr(
                    client, "last_trace_id", None
                )
        except Exception as exc:
            if current_span is not None:
                span = current_span()
                if span is not None:
                    latency_ms = int((time.time() - start_time) * 1000)
                    span.log(
                        output=str(exc),
                        metrics={"latency_ms": latency_ms},
                        metadata={"status": "error", "error": str(exc)},
                    )
            raise

        emit(AgentProgressUpdate(type="status", text="Synthesizing response..."))

        latency_ms = int((time.time() - start_time) * 1000)

        output = final_text.strip()
        if not output:
            output = "Sorry, I encountered an issue generating a response."

        if not trace_id and current_span is not None:
            span = current_span()
            trace_id = getattr(span, "id", None)

        if current_span is not None:
            span = current_span()
            if span is not None:
                span.log(
                    output=output,
                    metrics={
                        "latency_ms": latency_ms,
                        "tool_count": len(tool_calls_list),
                    },
                )

        return AgentResponse(
            content=output,
            tool_calls=tool_calls_list,
            llm_calls=1,
            latency_ms=latency_ms,
            model=self.model,
            trace_id=trace_id,
        )

    def _build_sdk_tools(
        self,
        tool_schemas: list[dict[str, Any]],
        emit: Callable[[AgentProgressUpdate], None],
        tool_calls_list: list[dict[str, Any]],
    ) -> list[Any]:
        sdk_tools: list[Any] = []

        def build_handler(
            tool_name: str, tool_description: str, input_schema: dict[str, Any]
        ) -> Any:
            async def handler(args: dict[str, Any]) -> dict[str, Any]:
                tool_input = args or {}
                tool_desc = describe_tool_call(tool_name, tool_input)

                emit(
                    AgentProgressUpdate(
                        type="tool_start",
                        tool_name=tool_name,
                        tool_input=tool_input,
                        description=tool_desc,
                    )
                )

                tool_start = time.time()
                result = await self.tool_executor.execute(tool_name, tool_input)
                tool_latency = int((time.time() - tool_start) * 1000)

                output_text = "".join(
                    block.get("text", "") if block.get("type") == "text" else json.dumps(block)
                    for block in result.content
                )
                output_preview = truncate(output_text, 500)

                tool_calls_list.append(
                    {
                        "tool_name": tool_name,
                        "tool_input": tool_input,
                        "tool_output": output_preview,
                        "is_error": result.is_error,
                        "latency_ms": tool_latency,
                    }
                )

                emit(
                    AgentProgressUpdate(
                        type="tool_end",
                        tool_name=tool_name,
                        is_error=result.is_error,
                        output_preview=output_preview,
                    )
                )

                return {
                    "content": result.content,
                    "is_error": result.is_error,
                }

            return self._decorate_tool(handler, tool_name, tool_description, input_schema)

        for schema in tool_schemas:
            name = schema["name"]
            description = schema.get("description", "")
            input_schema = schema.get("input_schema", {})
            sdk_tools.append(build_handler(name, description, input_schema))

        return sdk_tools

    def _decorate_tool(
        self,
        handler: Callable[[dict[str, Any]], Any],
        tool_name: str,
        tool_description: str,
        input_schema: dict[str, Any],
    ) -> Any:
        try:
            return tool(tool_name, tool_description, input_schema)(handler)
        except Exception:
            fallback_schema = self._simplify_schema(input_schema)
            return tool(tool_name, tool_description, fallback_schema)(handler)

    @staticmethod
    def _simplify_schema(input_schema: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(input_schema, dict):
            return input_schema

        properties = input_schema.get("properties")
        if not isinstance(properties, dict):
            return input_schema

        type_map = {
            "string": str,
            "number": float,
            "integer": int,
            "boolean": bool,
            "array": list,
            "object": dict,
        }

        simplified: dict[str, Any] = {}
        for key, schema in properties.items():
            if isinstance(schema, dict):
                simplified[key] = type_map.get(schema.get("type"), str)
            else:
                simplified[key] = str

        return simplified

    def _supports_option(self, option_name: str) -> bool:
        try:
            signature = inspect.signature(ClaudeAgentOptions)
        except (TypeError, ValueError):
            return False

        for param in signature.parameters.values():
            if param.kind == inspect.Parameter.VAR_KEYWORD:
                return True

        return option_name in signature.parameters

    def _build_agent_options(
        self,
        system_prompt: str,
        mcp_server: Any,
        allowed_tools: list[str],
    ) -> tuple[Any, bool]:
        options_kwargs: dict[str, Any] = {
            "model": self.model,
            "permission_mode": "bypassPermissions",
            "mcp_servers": {self._mcp_server_name: mcp_server},
            "allowed_tools": allowed_tools,
        }

        debug_enabled = os.environ.get("CLAUDE_SDK_DEBUG", 1)

        if self._supports_option("max_tokens"):
            options_kwargs["max_tokens"] = self.max_tokens
        if self._supports_option("temperature"):
            options_kwargs["temperature"] = self.temperature
        if self._supports_option("continue_conversation"):
            options_kwargs["continue_conversation"] = False
        if self._supports_option("env"):
            env = {}
            if self.api_key:
                env["ANTHROPIC_API_KEY"] = self.api_key
            if self._braintrust_api_key:
                env["BRAINTRUST_API_KEY"] = self._braintrust_api_key
            if self._braintrust_project:
                env["BRAINTRUST_PROJECT"] = self._braintrust_project
            if env:
                options_kwargs["env"] = env
        if debug_enabled:
            if self._supports_option("extra_args"):
                options_kwargs["extra_args"] = {"debug-to-stderr": None}
            if self._supports_option("stderr"):
                options_kwargs["stderr"] = lambda line: logger.warning("Claude CLI: %s", line)

        system_prompt_in_options = False
        if self._supports_option("system_prompt"):
            options_kwargs["system_prompt"] = system_prompt
            system_prompt_in_options = True

        return ClaudeAgentOptions(**options_kwargs), system_prompt_in_options

    @staticmethod
    def _format_conversation(messages: list[ConversationMessage]) -> str:
        lines: list[str] = []
        for message in messages:
            role = "User" if message.role == "user" else "Assistant"
            lines.append(f"{role}: {message.content}")
        return "\n".join(lines)

    @staticmethod
    def _extract_text_from_message(message: Any) -> str:
        if isinstance(message, str):
            return message

        if isinstance(message, dict):
            if "text" in message and isinstance(message["text"], str):
                return message["text"]
            if "content" in message:
                return ClaudeAgentService._extract_text_from_blocks(message["content"])

        content = getattr(message, "content", None)
        if content is not None:
            return ClaudeAgentService._extract_text_from_blocks(content)

        text = getattr(message, "text", None)
        if isinstance(text, str):
            return text

        return ""

    @staticmethod
    def _extract_text_from_blocks(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                parts.append(ClaudeAgentService._extract_text_from_block(block))
            return "".join(parts)
        return ""

    @staticmethod
    def _extract_text_from_block(block: Any) -> str:
        if isinstance(block, str):
            return block
        if isinstance(block, dict):
            if block.get("type") == "text":
                return block.get("text", "")
            return block.get("text", "") if "text" in block else ""

        block_type = getattr(block, "type", None)
        if block_type == "text":
            return getattr(block, "text", "") or ""
        return getattr(block, "text", "") if hasattr(block, "text") else ""

    async def close(self) -> None:
        """Close the service and cleanup resources."""
        await self.sefaria_client.close()


# Convenience function


def get_agent_service() -> ClaudeAgentService:
    """Get a new agent service instance."""
    return ClaudeAgentService()
