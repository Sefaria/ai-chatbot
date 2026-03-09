"""Turn orchestration for the V2 Claude agent runtime."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import braintrust
from braintrust import current_span

from ..prompts.prompt_fragments import ERROR_FALLBACK_MESSAGE
from .contracts import AgentProgressUpdate, AgentResponse, ConversationMessage, MessageContext
from .guardrail_gate import DefaultGuardrailGate
from .metrics_mapper import build_agent_response, build_braintrust_metrics, map_usage
from .progress import ProgressEmitter
from .prompt_pipeline import build_turn_prompt
from .sdk_options_builder import SDKOptionsBuilder
from .sdk_runner import ClaudeSDKRunner
from .tool_runtime import ToolRuntime
from .tool_schemas import get_all_tools
from .trace_logger import BraintrustTraceLogger


class TurnOrchestrator:
    """Coordinates a single turn: guardrail -> prompt -> tools -> SDK -> metrics."""

    def __init__(
        self,
        *,
        model: str,
        mcp_server_name: str,
        prompt_service: Any,
        create_mcp_server: Callable[..., Any],
        tool_runtime: ToolRuntime,
        options_builder: SDKOptionsBuilder,
        sdk_runner: ClaudeSDKRunner,
        guardrail_gate: DefaultGuardrailGate,
        trace_logger: BraintrustTraceLogger,
        logging_enabled: bool = True,
    ):
        self.model = model
        self.mcp_server_name = mcp_server_name
        self.prompt_service = prompt_service
        self.create_mcp_server = create_mcp_server
        self.tool_runtime = tool_runtime
        self.options_builder = options_builder
        self.sdk_runner = sdk_runner
        self.guardrail_gate = guardrail_gate
        self.trace_logger = trace_logger
        self.logging_enabled = logging_enabled

    async def run_turn(
        self,
        *,
        messages: list[ConversationMessage],
        core_prompt_id: str | None,
        on_progress: Callable[[AgentProgressUpdate], None] | None,
        context: MessageContext,
    ) -> AgentResponse:
        start_time = time.time()
        bt_span = current_span() if self.logging_enabled else braintrust.NOOP_SPAN
        emitter = ProgressEmitter(on_progress)

        last_user_message = next(
            (message.content for message in reversed(messages) if message.role == "user"),
            "",
        )

        self.trace_logger.log_input(
            bt_span=bt_span,
            user_message=last_user_message,
            context=context,
            model=self.model,
        )

        guardrail_response = await self.guardrail_gate.run_guardrail(
            bt_span=bt_span,
            user_message=last_user_message,
            context=context,
            start_time=start_time,
        )
        if guardrail_response:
            return guardrail_response

        core_prompt = self.prompt_service.get_core_prompt(prompt_id=core_prompt_id)
        prompt_result = build_turn_prompt(
            messages=messages,
            core_prompt=core_prompt.text,
            context=context,
        )

        tool_calls_list: list[dict[str, Any]] = []
        tools = get_all_tools()
        sdk_tools = self.tool_runtime.build_sdk_tools(
            tool_schemas=tools,
            emit=emitter.emit,
            tool_calls_list=tool_calls_list,
        )
        allowed_tools = [
            f"mcp__{self.mcp_server_name}__{tool_schema['name']}" for tool_schema in tools
        ]
        mcp_server = self.create_mcp_server(
            name=self.mcp_server_name,
            version="1.0.0",
            tools=sdk_tools,
        )

        options, system_prompt_in_options = self.options_builder.build(
            system_prompt=prompt_result.full_prompt,
            mcp_server=mcp_server,
            allowed_tools=allowed_tools,
        )
        self.trace_logger.log_prompt_metadata(
            bt_span=bt_span,
            core_prompt_id=core_prompt.prompt_id,
            core_prompt_version=core_prompt.version,
            system_prompt_in_options=system_prompt_in_options,
            summary_included=prompt_result.summary_included,
        )

        # Avoid sending the full prompt text if it's already included in the options
        # (e.g. via a system prompt) to save tokens and improve trace clarity.
        prompt_text = (
            prompt_result.full_prompt
            if not system_prompt_in_options
            else prompt_result.conversation_text
        )

        emitter.emit(AgentProgressUpdate(type="status", text="Thinking..."))
        try:
            sdk_result = await self.sdk_runner.run(options=options, prompt_text=prompt_text)
        except Exception as exc:
            latency_ms = int((time.time() - start_time) * 1000)
            self.trace_logger.log_error(bt_span=bt_span, exc=exc, latency_ms=latency_ms)
            raise

        emitter.emit(AgentProgressUpdate(type="status", text="Synthesizing response..."))

        latency_ms = int((time.time() - start_time) * 1000)
        output = sdk_result.final_text.strip() or ERROR_FALLBACK_MESSAGE
        trace_id = sdk_result.trace_id or bt_span.id
        usage = map_usage(sdk_result.usage)
        metrics = build_braintrust_metrics(
            latency_ms=latency_ms,
            tool_count=len(tool_calls_list),
            llm_call_count=sdk_result.llm_call_count,
            usage=usage,
            total_cost_usd=sdk_result.total_cost_usd,
        )
        self.trace_logger.log_success(
            bt_span=bt_span,
            content=output,
            tool_calls=tool_calls_list,
            metrics=metrics,
        )

        return build_agent_response(
            content=output,
            tool_calls=tool_calls_list,
            latency_ms=latency_ms,
            model=self.model,
            trace_id=trace_id,
            llm_call_count=sdk_result.llm_call_count,
            usage=usage,
            total_cost_usd=sdk_result.total_cost_usd,
        )
