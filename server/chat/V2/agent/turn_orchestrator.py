"""Turn orchestration for the V2 Claude agent runtime."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from braintrust import current_span
from django.conf import settings

from ..pricing import compute_cost
from ..prompts.prompt_fragments import ERROR_FALLBACK_MESSAGE
from .contracts import AgentProgressUpdate, AgentResponse, ConversationMessage, MessageContext
from .guardrail_gate import DefaultGuardrailGate
from .metrics_mapper import build_agent_response, build_braintrust_metrics, map_usage
from .progress import ProgressEmitter
from .prompt_pipeline import build_turn_prompt
from .router import Router
from .sdk_options_builder import SDKOptionsBuilder
from .sdk_runner import ClaudeSDKRunner
from .tool_runtime import ToolRuntime
from .tool_schemas import get_all_tools
from .trace_logger import BraintrustTraceLogger

logger = logging.getLogger("chat.agent")


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
        router: Router,
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
        self.router = router
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
        # The tracing guard (tracing_guard.py) ensures start_span returns
        # NOOP_SPAN in load-test threads, so current_span() is safe to call
        # unconditionally here.
        bt_span = current_span()
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

        auxiliary_cost = 0.0

        guardrail_gate_result = await self.guardrail_gate.run_guardrail(
            bt_span=bt_span,
            user_message=last_user_message,
            context=context,
            start_time=start_time,
        )
        guardrail_cost = compute_cost(
            guardrail_gate_result.model,
            guardrail_gate_result.input_tokens,
            guardrail_gate_result.output_tokens,
        )
        if guardrail_cost:
            auxiliary_cost += guardrail_cost
        elif guardrail_gate_result.input_tokens > 0:
            logger.warning(f"No pricing for guardrail model: {guardrail_gate_result.model}")

        if guardrail_gate_result.blocked_response:
            guardrail_gate_result.blocked_response.total_cost_usd = auxiliary_cost or None
            return guardrail_gate_result.blocked_response

        router_prompt_id, route, messages, router_usage = await self.router.run_router(
            bt_span, last_user_message, messages
        )
        router_cost = compute_cost(
            router_usage["model"],
            router_usage["input_tokens"],
            router_usage["output_tokens"],
        )
        if router_cost:
            auxiliary_cost += router_cost
        elif router_usage["input_tokens"] > 0:
            logger.warning(f"No pricing for router model: {router_usage['model']}")

        if router_prompt_id:
            core_prompt_id = router_prompt_id

        # Fetch the response-format prompt and pass it as a template variable.
        # Braintrust prompts that include {{response_format}} will get it substituted.
        response_format = self.prompt_service.get_core_prompt(
            prompt_id=settings.RESPONSE_FORMAT_PROMPT_SLUG
        )
        core_prompt = self.prompt_service.get_core_prompt(
            prompt_id=core_prompt_id,
            build_vars={"response_format": response_format.text},
        )

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
            route=route,
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

        total_cost_usd = sdk_result.total_cost_usd
        if total_cost_usd is not None:
            total_cost_usd += auxiliary_cost
        elif auxiliary_cost > 0:
            total_cost_usd = auxiliary_cost

        return build_agent_response(
            content=output,
            tool_calls=tool_calls_list,
            latency_ms=latency_ms,
            model=self.model,
            trace_id=trace_id,
            llm_call_count=sdk_result.llm_call_count,
            usage=usage,
            total_cost_usd=total_cost_usd,
        )
