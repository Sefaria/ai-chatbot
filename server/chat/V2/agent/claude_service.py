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

from ..guardrail import parse_guardrail_response
from ..guardrail.guardrail_service import GuardrailResult
from ..prompts import PromptService, get_prompt_service
from ..prompts.prompt_fragments import (
    ERROR_FALLBACK_MESSAGE,
    GUARDRAIL_MALFORMED_REASON,
    GUARDRAIL_REJECTION_MESSAGE,
    GUARDRAIL_REJECTION_WITH_REASON,
    GUARDRAIL_UNAVAILABLE_REASON,
    build_prompt,
)
from ..utils import get_anthropic_client, get_braintrust_config
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


def _sum_costs(*costs: float | None) -> float | None:
    """Sum cost values, returning None if all are None."""
    values = [c for c in costs if c is not None]
    return sum(values) if values else None


# ---------------------------------------------------------------------------
# Main service
# ---------------------------------------------------------------------------


class ClaudeAgentService:
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

        self.client = get_anthropic_client(api_key)
        self.prompt_service = prompt_service or get_prompt_service()
        bt = get_braintrust_config()
        self.braintrust_api_key = bt.api_key
        self.braintrust_project = bt.project

        # The SDK reads the key from the environment, so ensure it's set.
        api_key_str = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            os.environ["ANTHROPIC_API_KEY"] = api_key_str

        from django.conf import settings as django_settings

        self.model = model or django_settings.AGENT_MODEL
        self.max_iterations = max_iterations
        self.max_tokens = max_tokens
        self.temperature = temperature

        # Sefaria HTTP client is shared between the executor and this service
        # so we get connection reuse across tool calls within a single turn.
        self.sefaria_client = SefariaClient()
        self.tool_executor = SefariaToolExecutor(self.sefaria_client)

        # All tools are registered under a single MCP server named "sefaria".
        # The SDK prefixes tool names as mcp__sefaria__<tool_name>.
        self._mcp_server_name = "sefaria"

        self._setup_braintrust_tracing()

    def _setup_braintrust_tracing(self) -> None:
        """Ensure Braintrust tracing is active for this thread.

        Two concerns:
        1. SDK monkey-patching (global, once per process via _BRAINTRUST_SETUP_DONE)
        2. Setting current_logger in this thread's ContextVar so @braintrust.traced
           produces real spans. init_logger stores the logger in a ContextVar, which
           is per-thread — so every new request thread needs its own init_logger call.
        """
        global _BRAINTRUST_SETUP_DONE
        if not _BRAINTRUST_SETUP_DONE:
            setup_claude_agent_sdk(project=self.braintrust_project, api_key=self.braintrust_api_key)
            _BRAINTRUST_SETUP_DONE = True
        elif not braintrust.current_logger():
            braintrust.init_logger(project=self.braintrust_project, api_key=self.braintrust_api_key)

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
        """Runs one full agent turn inside a single ClaudeSDKClient subprocess.

        Two-phase flow on one subprocess for accurate cost tracking:
        1. Guardrail phase (Haiku) — hard gate, short-circuits if blocked
        2. Agent phase (Sonnet) — main response with tools

        Both phases use the same CLI subprocess so total_cost_usd is computed
        by the same pricing logic (the Agent SDK CLI binary).
        """
        start_time = time.time()
        bt_span = current_span()

        def emit(update: AgentProgressUpdate) -> None:
            """Safe wrapper — swallows callback errors so they don't kill the turn."""
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

        # --- Build MCP tools (needed for agent phase, harmless during guardrail) ---
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

        # --- Configure SDK options (shared by both phases) ---
        # system_prompt is empty — both phases put their prompts in query text.
        # The guardrail model is set first; we switch to the agent model after.
        options = self._build_agent_options(
            mcp_server=mcp_server,
            allowed_tools=allowed_tools,
        )

        guardrail_cost: float | None = None

        try:
            async with ClaudeSDKClient(options=options) as client:
                # --- Phase 1: Guardrail (Haiku) — hard gate ---
                guardrail_response = await self._run_guardrail_via_sdk(
                    client,
                    bt_span,
                    last_user_message,
                    context,
                    start_time,
                )
                guardrail_cost = guardrail_response.total_cost_usd

                if guardrail_response.blocked:
                    return guardrail_response.agent_response

                # --- Phase 2: Agent response (Sonnet) ---
                await client.set_model(self.model)

                core_prompt = self.prompt_service.get_core_prompt(prompt_id=core_prompt_id)
                conversation_text = self._format_conversation(messages)
                full_prompt, summary_included = build_prompt(
                    conversation_text,
                    core_prompt=core_prompt.text,
                    summary_text=context.summary_text,
                    page_url=context.page_url,
                )

                bt_span.log(
                    metadata={
                        "core_prompt_id": core_prompt.prompt_id,
                        "core_prompt_version": core_prompt.version,
                        "summary_included": summary_included,
                    }
                )

                emit(AgentProgressUpdate(type="status", text="Thinking..."))

                final_text = ""
                llm_call_count = 0
                result_usage: dict[str, Any] | None = None
                agent_cost: float | None = None

                await client.query(full_prompt)
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        llm_call_count += 1
                    if isinstance(message, ResultMessage):
                        result_usage = message.usage
                        agent_cost = message.total_cost_usd
                    else:
                        chunk = self._extract_text_from_message(message)
                        if chunk:
                            final_text += chunk

                trace_id = getattr(client, "trace_id", None) or getattr(
                    client, "last_trace_id", None
                )

        except Exception as exc:
            latency_ms = int((time.time() - start_time) * 1000)
            bt_span.log(
                output=str(exc),
                metrics={"latency_ms": latency_ms},
                metadata={"status": "error", "error": str(exc)},
            )
            raise

        emit(AgentProgressUpdate(type="status", text="Synthesizing response..."))

        # --- Finalize and log metrics ---
        latency_ms = int((time.time() - start_time) * 1000)

        output = final_text.strip()
        if not output:
            output = ERROR_FALLBACK_MESSAGE

        if not trace_id:
            trace_id = bt_span.id

        # Sum costs from both phases (guardrail + agent).
        total_cost_usd = _sum_costs(guardrail_cost, agent_cost)

        input_tokens = None
        output_tokens = None
        cache_creation_tokens = None
        cache_read_tokens = None
        if result_usage:
            input_tokens = result_usage.get("input_tokens")
            output_tokens = result_usage.get("output_tokens")
            cache_creation_tokens = result_usage.get("cache_creation_input_tokens")
            cache_read_tokens = result_usage.get("cache_read_input_tokens")

        refs = extract_refs(tool_calls_list)
        span_output: dict[str, Any] = {
            "content": output,
            "ref_count": len(refs),
            "tool_count": len(tool_calls_list),
        }
        span_log_metadata: dict[str, Any] = {
            "refs": refs,
            "tool_calls": tool_calls_list,
        }
        metrics: dict[str, Any] = {
            "latency_ms": latency_ms,
            "tool_count": len(tool_calls_list),
        }
        if llm_call_count:
            metrics["llm_calls"] = llm_call_count
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
        bt_span.log(output=span_output, metrics=metrics, metadata=span_log_metadata)

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
    # Guardrail helper
    # -------------------------------------------------------------------

    @dataclass
    class _GuardrailPhaseResult:
        """Internal result from the guardrail SDK phase."""

        blocked: bool
        agent_response: AgentResponse | None  # set only when blocked
        total_cost_usd: float | None

    async def _run_guardrail_via_sdk(
        self,
        client: ClaudeSDKClient,
        bt_span,
        user_message: str,
        context: MessageContext,
        start_time: float,
    ) -> _GuardrailPhaseResult:
        """Run the guardrail as the first query on the shared SDK client.

        Uses set_model() to switch to the guardrail model (Haiku), runs the
        classification query, then returns the result. Fails closed on any error.
        """
        from django.conf import settings as django_settings

        guardrail_span = bt_span.start_span(name="guardrail", type="task")

        enriched_message, _ = build_prompt(
            user_message, summary_text=context.summary_text, page_url=context.page_url
        )
        guardrail_cost: float | None = None

        try:
            # Switch to guardrail model (Haiku).
            await client.set_model(django_settings.GUARDRAIL_MODEL)

            # Build the guardrail query: system prompt + user message in one text.
            guardrail_prompt = self.prompt_service.get_core_prompt(
                prompt_id=django_settings.GUARDRAIL_PROMPT_SLUG
            ).text
            query_text = f"{guardrail_prompt}\n\n---\n\nUser message:\n{enriched_message}"

            # Run the guardrail classification.
            guardrail_text = ""
            await client.query(query_text)
            async for message in client.receive_response():
                if isinstance(message, ResultMessage):
                    guardrail_cost = message.total_cost_usd
                else:
                    chunk = self._extract_text_from_message(message)
                    if chunk:
                        guardrail_text += chunk

            guardrail_result = parse_guardrail_response(guardrail_text)

        except Exception as exc:
            # Fail closed: any SDK/parsing error blocks the message.
            logger.error(f"Guardrail: SDK call failed: {exc}")
            guardrail_result = GuardrailResult(allowed=False, reason=GUARDRAIL_UNAVAILABLE_REASON)

        guardrail_span.log(
            input={"message": enriched_message},
            output={"allowed": guardrail_result.allowed, "reason": guardrail_result.reason},
            metadata={"guardrail_blocked": not guardrail_result.allowed},
            metrics={"total_cost_usd": guardrail_cost} if guardrail_cost else {},
        )
        guardrail_span.end()

        if guardrail_result.allowed:
            return self._GuardrailPhaseResult(
                blocked=False, agent_response=None, total_cost_usd=guardrail_cost
            )

        # Blocked — build rejection response.
        logger.info(f"Guardrail blocked message: {guardrail_result.reason}")
        internal_reasons = {GUARDRAIL_UNAVAILABLE_REASON, GUARDRAIL_MALFORMED_REASON}
        reason = guardrail_result.reason
        if reason and reason not in internal_reasons:
            rejection = GUARDRAIL_REJECTION_WITH_REASON.format(reason=reason)
        else:
            rejection = GUARDRAIL_REJECTION_MESSAGE

        latency_ms = int((time.time() - start_time) * 1000)
        bt_span.log(
            output={"content": rejection, "guardrail_blocked": True},
            metrics={
                "latency_ms": latency_ms,
                **({"total_cost_usd": guardrail_cost} if guardrail_cost else {}),
            },
        )
        return self._GuardrailPhaseResult(
            blocked=True,
            agent_response=AgentResponse(
                content=rejection,
                tool_calls=[],
                latency_ms=latency_ms,
                trace_id=bt_span.id,
                total_cost_usd=guardrail_cost,
            ),
            total_cost_usd=guardrail_cost,
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
                type_name = schema.get("type", "string")
                simplified[key] = (
                    type_map.get(type_name, str) if isinstance(type_name, str) else str
                )
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
        mcp_server: Any,
        allowed_tools: list[str],
    ) -> Any:
        """Construct ClaudeAgentOptions for the shared two-phase SDK client.

        system_prompt is set to empty because both the guardrail and agent
        phases put their prompts directly in the query text. The model starts
        as the guardrail model; _send_message_inner switches to the agent
        model via set_model() between phases.
        """
        from django.conf import settings as django_settings

        options_kwargs: dict[str, Any] = {
            # Start with guardrail model; switched to agent model after guardrail.
            "model": django_settings.GUARDRAIL_MODEL,
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
            options_kwargs["env"] = {
                "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
                "BRAINTRUST_API_KEY": self.braintrust_api_key,
                "BRAINTRUST_PROJECT": self.braintrust_project,
            }
        if debug_enabled:
            if self._supports_option("extra_args"):
                options_kwargs["extra_args"] = {"debug-to-stderr": None}
            if self._supports_option("stderr"):
                options_kwargs["stderr"] = lambda line: logger.warning("Claude CLI: %s", line)

        # Empty system prompt — both phases put prompts in query text.
        if self._supports_option("system_prompt"):
            options_kwargs["system_prompt"] = ""

        return ClaudeAgentOptions(**options_kwargs)

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
