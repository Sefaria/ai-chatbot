"""
Claude Agent Service — the main runtime for the Sefaria chatbot.

Flow overview (see also docs/ARCHITECTURE.md):

    API view (views.py / anthropic_views.py)
        → ClaudeAgentService.send_message()
            → loads system prompt from Braintrust (prompt_service.py)
            → builds SDK tools that wrap SefariaToolExecutor (tool_executor.py)
            → runs the Claude Agent SDK client (query → receive_response loop)
            → emits progress callbacks for SSE streaming
            → returns AgentResponse with content + metadata

Key integration points:
- Claude Agent SDK: manages the LLM ↔ tool-call loop
- Braintrust: tracing/observability (wraps SDK via monkey-patch)
- MCP server: tools are exposed to the SDK as an in-process MCP server
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import braintrust
from braintrust import current_span
from braintrust.wrappers.claude_agent_sdk import setup_claude_agent_sdk
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, create_sdk_mcp_server, tool
from claude_agent_sdk.types import AssistantMessage, ResultMessage

from ..base_llm_service import BaseLLMService
from ..guardrail import get_guardrail_service
from ..prompts import PromptService
from ..prompts.prompt_fragments import (
    ERROR_FALLBACK_MESSAGE,
    GUARDRAIL_REJECTION_MESSAGE,
    build_system_prompt,
)
from .sefaria_client import SefariaClient
from .tool_executor import SefariaToolExecutor, describe_tool_call
from .tool_schemas import get_all_tools

logger = logging.getLogger("chat.agent")

# Global flag to ensure setup_claude_agent_sdk is only called once per process.
# IMPORTANT: This must be a global (not thread-local) because setup_claude_agent_sdk
# patches the SDK classes globally. Using thread-local would cause the SDK to be
# wrapped multiple times (once per thread), creating deeply nested spans.
_BRAINTRUST_SETUP_DONE = False

# ---------------------------------------------------------------------------
# Data classes — these are the public API types passed between layers
# ---------------------------------------------------------------------------


@dataclass
class AgentProgressUpdate:
    """Streamed to the client via SSE during a single chat turn.

    The views layer converts these into SSE events so the frontend can
    show real-time status ("Thinking…", "Searching texts for X…", etc.).
    """

    type: str  # 'status', 'tool_start', 'tool_end', 'complete'
    text: str | None = None
    tool_name: str | None = None
    tool_input: dict | None = None
    description: str | None = None  # human-readable tool call label
    is_error: bool | None = None
    output_preview: str | None = None


@dataclass
class ConversationMessage:
    """A single turn in the conversation history passed to the agent."""

    role: str  # 'user' or 'assistant'
    content: str


@dataclass
class MessageContext:
    """Sideband context injected into the system prompt (not part of the messages array)."""

    summary_text: str | None = None  # rolling conversation summary
    page_url: str | None = None  # Sefaria page the user is viewing
    session_id: str | None = None  # used for Braintrust span metadata


@dataclass
class AgentResponse:
    """Returned by send_message(); consumed by views + logging layers."""

    content: str
    tool_calls: list[dict[str, Any]]  # recorded tool invocations for logging
    latency_ms: int
    model: str | None = None
    trace_id: str | None = None  # Braintrust trace ID for feedback linking
    llm_calls: int | None = None
    # Token usage from ResultMessage (standard Anthropic fields)
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cache_read_tokens: int | None = None
    total_cost_usd: float | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    """Truncate text to max length, appending '...' if trimmed."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------


class ClaudeAgentService(BaseLLMService):
    """Orchestrates a single chat turn: prompt → SDK → tool calls → response.

    One instance is created per request by `get_agent_service()`. It holds
    the Sefaria HTTP client and tool executor, configures the Claude Agent
    SDK, and drives the query → receive_response loop.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        max_iterations: int = 10,
        max_tokens: int = 8000,
        temperature: float = 0.7,
        prompt_service: PromptService | None = None,
    ):
        if (
            ClaudeAgentOptions is None
            or ClaudeSDKClient is None
            or create_sdk_mcp_server is None
            or tool is None
        ):
            raise RuntimeError(
                "claude-agent-sdk is required. Install with `pip install claude-agent-sdk`."
            )

        super().__init__(api_key=api_key, prompt_service=prompt_service)
        self._ensure_client()
        # The SDK reads the key from the environment, so ensure it's set.
        if not os.environ.get("ANTHROPIC_API_KEY"):
            os.environ["ANTHROPIC_API_KEY"] = self.api_key

        from django.conf import settings as django_settings

        self.model = model or django_settings.AGENT_MODEL
        self.max_iterations = max_iterations
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._braintrust_api_key = os.environ.get("BRAINTRUST_API_KEY")
        if not self._braintrust_api_key:
            raise RuntimeError("BRAINTRUST_API_KEY environment variable is required")
        self._braintrust_project = os.environ.get("BRAINTRUST_PROJECT", "On Site Agent")

        # Sefaria HTTP client is shared between the executor and this service
        # so we get connection reuse across tool calls within a single turn.
        self.sefaria_client = SefariaClient()
        self.tool_executor = SefariaToolExecutor(self.sefaria_client)

        # All tools are registered under a single MCP server named "sefaria".
        # The SDK prefixes tool names as mcp__sefaria__<tool_name>.
        self._mcp_server_name = "sefaria"

        self._setup_braintrust_tracing()

    def _setup_braintrust_tracing(self) -> None:
        """One-time setup: monkey-patches the Claude Agent SDK to emit Braintrust spans.

        Uses a global flag (_BRAINTRUST_SETUP_DONE) because the patch is process-wide.
        """
        global _BRAINTRUST_SETUP_DONE
        if _BRAINTRUST_SETUP_DONE:
            return

        setup_claude_agent_sdk(project=self._braintrust_project, api_key=self._braintrust_api_key)
        _BRAINTRUST_SETUP_DONE = True

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    async def send_message(
        self,
        messages: list[ConversationMessage],
        core_prompt_id: str | None = None,
        on_progress: Callable[[AgentProgressUpdate], None] | None = None,
        context: MessageContext | None = None,
    ) -> AgentResponse:
        """Entry point for a single chat turn.

        The entire turn is wrapped in a Braintrust traced span named
        "chat-agent" so that LLM calls and tool calls appear as children
        in the Braintrust dashboard.
        """
        context = context or MessageContext()

        @braintrust.traced(name="chat-agent", type="task")
        async def run() -> AgentResponse:
            return await self._send_message_inner(
                messages=messages,
                core_prompt_id=core_prompt_id,
                on_progress=on_progress,
                context=context,
            )

        return await run()

    # -------------------------------------------------------------------
    # Core turn logic
    # -------------------------------------------------------------------

    async def _send_message_inner(
        self,
        *,
        messages: list[ConversationMessage],
        core_prompt_id: str | None,
        on_progress: Callable[[AgentProgressUpdate], None] | None,
        context: MessageContext,
    ) -> AgentResponse:
        """Runs one full agent turn: guardrail → prompt assembly → SDK loop → response.

        Steps:
        0. Run guardrail check — short-circuits with a rejection if blocked
        1. Build the system prompt (core from Braintrust + optional summary/page context)
        2. Wrap each Sefaria tool as an SDK-compatible MCP tool
        3. Configure and launch the ClaudeSDKClient
        4. Stream text chunks from the SDK, accumulating the final response
        5. Log metrics to Braintrust span
        """
        start_time = time.time()

        # Grab the Braintrust span once — all logging below uses this reference.
        bt_span = current_span()

        def emit(update: AgentProgressUpdate) -> None:
            """Safe wrapper — swallows callback errors so they don't kill the turn."""
            if not on_progress:
                return
            try:
                on_progress(update)
            except Exception as exc:
                logger.warning(f"Progress callback error: {exc}")

        # --- Guardrail: check message before running the agent ----------
        last_user_message = next(
            (message.content for message in reversed(messages) if message.role == "user"),
            "",
        )

        # Log input early so the chat-agent span has context even if the guardrail blocks.
        span_input: dict[str, Any] = {"message": last_user_message}
        if context.page_url:
            span_input["page_url"] = context.page_url
        if context.summary_text:
            span_input["summary"] = context.summary_text
        span_metadata: dict[str, Any] = {"model": self.model}
        if context.session_id:
            span_metadata["session_id"] = context.session_id
        bt_span.log(input=span_input, metadata=span_metadata)

        guardrail_span = bt_span.start_span(name="guardrail")
        guardrail_result = await asyncio.to_thread(
            get_guardrail_service().check_message, last_user_message
        )
        guardrail_span.log(
            input={"message": last_user_message},
            output={"allowed": guardrail_result.allowed, "reason": guardrail_result.reason},
            metadata={"guardrail_blocked": not guardrail_result.allowed},
        )
        guardrail_span.end()

        if not guardrail_result.allowed:
            logger.info(f"Guardrail blocked message: {guardrail_result.reason}")
            latency_ms = int((time.time() - start_time) * 1000)
            bt_span.log(
                output={"content": GUARDRAIL_REJECTION_MESSAGE, "guardrail_blocked": True},
                metrics={"latency_ms": latency_ms},
            )
            return AgentResponse(
                content=GUARDRAIL_REJECTION_MESSAGE,
                tool_calls=[],
                latency_ms=latency_ms,
                trace_id=bt_span.id,
            )

        # --- Step 1: Assemble the system prompt -------------------------
        core_prompt = self.prompt_service.get_core_prompt(prompt_id=core_prompt_id)
        system_prompt, summary_included = build_system_prompt(
            core_prompt.text,
            summary_text=context.summary_text,
            page_url=context.page_url,
        )

        # --- Step 2: Build MCP tools ------------------------------------
        # tool_calls_list is mutated by the tool handlers to record each invocation.
        tool_calls_list: list[dict[str, Any]] = []
        tools = get_all_tools()
        sdk_tools = self._build_sdk_tools(tools, emit, tool_calls_list)

        # The SDK expects tool names prefixed with mcp__<server>__<tool>.
        allowed_tools = [
            f"mcp__{self._mcp_server_name}__{tool_schema['name']}" for tool_schema in tools
        ]
        mcp_server = create_sdk_mcp_server(
            name=self._mcp_server_name,
            version="1.0.0",
            tools=sdk_tools,
        )

        # --- Step 3: Configure SDK options ------------------------------
        options, system_prompt_in_options = self._build_agent_options(
            system_prompt=system_prompt,
            mcp_server=mcp_server,
            allowed_tools=allowed_tools,
        )

        # Log prompt-specific metadata (input already logged before guardrail).
        bt_span.log(
            metadata={
                "core_prompt_id": core_prompt.prompt_id,
                "core_prompt_version": core_prompt.version,
                "core_prompt_in_options": system_prompt_in_options,
                "summary_included": summary_included,
            }
        )

        # If the SDK version doesn't support system_prompt in options,
        # prepend it to the conversation text as a fallback.
        prompt_text = self._format_conversation(messages)
        if not system_prompt_in_options:
            prompt_text = f"{system_prompt}\n\n{prompt_text}" if prompt_text else system_prompt

        # --- Step 4: Run the SDK query → response loop ------------------
        emit(AgentProgressUpdate(type="status", text="Thinking..."))

        try:
            final_text = ""
            trace_id = None
            llm_call_count = 0
            result_usage: dict[str, Any] | None = None
            total_cost_usd: float | None = None
            async with ClaudeSDKClient(options=options) as client:
                await client.query(prompt_text)
                # The SDK may call tools internally (triggering our MCP handlers)
                # and then stream the final text response here.
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        llm_call_count += 1
                    if isinstance(message, ResultMessage):
                        result_usage = message.usage
                        total_cost_usd = message.total_cost_usd
                    else:
                        chunk = self._extract_text_from_message(message)
                        if chunk:
                            final_text += chunk
                trace_id = getattr(client, "trace_id", None) or getattr(
                    client, "last_trace_id", None
                )
        except Exception as exc:
            # Record the error in the Braintrust span before re-raising
            latency_ms = int((time.time() - start_time) * 1000)
            bt_span.log(
                output=str(exc),
                metrics={"latency_ms": latency_ms},
                metadata={"status": "error", "error": str(exc)},
            )
            raise

        emit(AgentProgressUpdate(type="status", text="Synthesizing response..."))

        # --- Step 5: Finalize and log metrics ---------------------------
        latency_ms = int((time.time() - start_time) * 1000)

        output = final_text.strip()
        if not output:
            output = ERROR_FALLBACK_MESSAGE

        # Fall back to the Braintrust span ID if the SDK didn't provide one
        if not trace_id:
            trace_id = bt_span.id

        # Extract token counts from ResultMessage.usage (standard Anthropic format)
        input_tokens = None
        output_tokens = None
        cache_creation_tokens = None
        cache_read_tokens = None
        if result_usage:
            input_tokens = result_usage.get("input_tokens")
            output_tokens = result_usage.get("output_tokens")
            cache_creation_tokens = result_usage.get("cache_creation_input_tokens")
            cache_read_tokens = result_usage.get("cache_read_input_tokens")

        # Log output, metrics, and metadata to the Braintrust span.
        refs = extract_refs(tool_calls_list)
        span_output: dict[str, Any] = {
            "content": output,
            "ref_count": len(refs),
            "tool_count": len(tool_calls_list),
        }
        span_metadata: dict[str, Any] = {
            "refs": refs,
            "tool_calls": tool_calls_list,
        }
        metrics: dict[str, Any] = {
            "latency_ms": latency_ms,
            "tool_count": len(tool_calls_list),
        }
        if llm_call_count:
            metrics["llm_calls"] = llm_call_count
        # Token metrics using Braintrust standard names.
        # Convention: prompt_tokens includes cache tokens.
        if input_tokens is not None:
            prompt_tokens = input_tokens + (cache_read_tokens or 0) + (cache_creation_tokens or 0)
            metrics["prompt_tokens"] = prompt_tokens
            metrics["completion_tokens"] = output_tokens or 0
            metrics["tokens"] = prompt_tokens + (output_tokens or 0)
        if cache_read_tokens is not None:
            metrics["prompt_cached_tokens"] = cache_read_tokens
        if cache_creation_tokens is not None:
            metrics["prompt_cache_creation_tokens"] = cache_creation_tokens
        if total_cost_usd is not None:
            metrics["total_cost_usd"] = total_cost_usd
        bt_span.log(output=span_output, metrics=metrics, metadata=span_metadata)

        return AgentResponse(
            content=output,
            tool_calls=tool_calls_list,
            latency_ms=latency_ms,
            model=self.model,
            trace_id=trace_id,
            llm_calls=llm_call_count or None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_tokens=cache_creation_tokens,
            cache_read_tokens=cache_read_tokens,
            total_cost_usd=total_cost_usd,
        )

    # -------------------------------------------------------------------
    # Tool wiring — bridging our Sefaria tools into the Claude Agent SDK
    # -------------------------------------------------------------------

    def _build_sdk_tools(
        self,
        tool_schemas: list[dict[str, Any]],
        emit: Callable[[AgentProgressUpdate], None],
        tool_calls_list: list[dict[str, Any]],
    ) -> list[Any]:
        """Create SDK-compatible tool handlers from our JSON tool schemas.

        Each handler:
        1. Emits a 'tool_start' progress event (for SSE streaming)
        2. Delegates to SefariaToolExecutor.execute()
        3. Records the call in tool_calls_list (for logging/metrics)
        4. Emits a 'tool_end' progress event
        5. Returns the result to the SDK so Claude can use it
        """
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

                # Flatten content blocks into a single string for the preview
                output_text = "".join(
                    block.get("text", "") if block.get("type") == "text" else json.dumps(block)
                    for block in result.content
                )
                output_preview = truncate(output_text, 500)

                # Record for post-turn logging (persisted alongside the response)
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
        """Register a handler with the SDK's @tool decorator.

        Falls back to a simplified schema if the SDK rejects our JSON Schema
        (some SDK versions only accept Python type annotations).
        """
        try:
            return tool(tool_name, tool_description, input_schema)(handler)
        except Exception:
            fallback_schema = self._simplify_schema(input_schema)
            return tool(tool_name, tool_description, fallback_schema)(handler)

    @staticmethod
    def _simplify_schema(input_schema: dict[str, Any]) -> dict[str, Any]:
        """Convert JSON Schema properties to bare Python types for older SDK versions."""
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

    # -------------------------------------------------------------------
    # SDK option construction
    # -------------------------------------------------------------------

    def _supports_option(self, option_name: str) -> bool:
        """Check if the installed SDK version accepts a given option.

        The SDK evolves rapidly; we introspect ClaudeAgentOptions to stay
        compatible without pinning an exact version.
        """
        try:
            signature = inspect.signature(ClaudeAgentOptions)
        except (TypeError, ValueError):
            return False

        # If the constructor accepts **kwargs, any option is allowed
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
        """Construct ClaudeAgentOptions, feature-detecting supported params.

        Returns (options, system_prompt_in_options). If the SDK doesn't support
        system_prompt as an option, the caller must prepend it to the conversation.
        """
        options_kwargs: dict[str, Any] = {
            "model": self.model,
            # bypassPermissions: the SDK normally prompts for user approval
            # of tool calls; we skip that since this is a server-side agent.
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
            # Pass API keys into the SDK subprocess environment
            env = {
                "ANTHROPIC_API_KEY": self.api_key,
                "BRAINTRUST_API_KEY": self._braintrust_api_key,
                "BRAINTRUST_PROJECT": self._braintrust_project,
            }
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

    # -------------------------------------------------------------------
    # Message formatting / text extraction
    # -------------------------------------------------------------------

    @staticmethod
    def _format_conversation(messages: list[ConversationMessage]) -> str:
        """Serialize the conversation history into a plain-text prompt for the SDK."""
        lines: list[str] = []
        for message in messages:
            role = "User" if message.role == "user" else "Assistant"
            lines.append(f"{role}: {message.content}")
        return "\n".join(lines)

    @staticmethod
    def _extract_text_from_message(message: Any) -> str:
        """Extract plain text from an SDK response message.

        The SDK can return messages in several shapes (str, dict with 'text'
        or 'content', or an object with .content/.text attributes). We try
        each in order.
        """
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
        """Concatenate text from a list of content blocks (Anthropic format)."""
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
        """Extract text from a single content block (dict or object)."""
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
        """Close the service and cleanup resources (HTTP connections, etc.)."""
        await self.sefaria_client.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_agent_service() -> ClaudeAgentService:
    """Create a fresh agent service instance (one per request)."""
    return ClaudeAgentService()
