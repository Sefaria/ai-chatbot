"""Guardrail gate for agent turns."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from ..guardrail import get_guardrail_service
from ..prompts.prompt_fragments import (
    GUARDRAIL_MALFORMED_REASON,
    GUARDRAIL_REJECTION_FALLBACK,
    GUARDRAIL_UNAVAILABLE_REASON,
    build_prompt,
)
from .contracts import AgentResponse, MessageContext


@dataclass
class GuardrailGateResult:
    """Result of the guardrail gate check."""

    blocked_response: AgentResponse | None
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


class DefaultGuardrailGate:
    """Runs guardrail checks and maps blocked decisions into AgentResponse."""

    def __init__(self, *, logger: logging.Logger | None = None):
        self.logger = logger or logging.getLogger("chat.agent")

    async def run_guardrail(
        self,
        *,
        bt_span: Any,
        user_message: str,
        context: MessageContext,
        start_time: float,
    ) -> GuardrailGateResult:
        enriched_message, _ = build_prompt(
            user_message,
            summary_text=context.summary_text,
            page_url=context.page_url,
        )

        guardrail_span = bt_span.start_span(name="guardrail", type="task")
        guardrail_result = await asyncio.to_thread(
            get_guardrail_service().check_message, enriched_message
        )
        guardrail_span.log(
            input={"message": enriched_message},
            output={"allowed": guardrail_result.allowed, "reason": guardrail_result.reason},
            metadata={"guardrail_blocked": not guardrail_result.allowed},
        )
        guardrail_span.end()

        usage = {
            "input_tokens": guardrail_result.input_tokens,
            "output_tokens": guardrail_result.output_tokens,
            "model": guardrail_result.model,
        }

        if guardrail_result.allowed:
            return GuardrailGateResult(blocked_response=None, **usage)

        self.logger.info(f"Guardrail blocked message: {guardrail_result.reason}")
        internal_reasons = {GUARDRAIL_UNAVAILABLE_REASON, GUARDRAIL_MALFORMED_REASON}
        reason = guardrail_result.reason
        if reason and reason not in internal_reasons:
            rejection = reason
        else:
            rejection = GUARDRAIL_REJECTION_FALLBACK

        latency_ms = int((time.time() - start_time) * 1000)
        bt_span.log(
            output={"content": rejection, "guardrail_blocked": True},
            metrics={"latency_ms": latency_ms},
        )
        return GuardrailGateResult(
            blocked_response=AgentResponse(
                content=rejection,
                tool_calls=[],
                latency_ms=latency_ms,
                trace_id=bt_span.id,
            ),
            **usage,
        )
