"""Guardrail gate for agent turns."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..guardrail import get_guardrail_service
from ..prompts.prompt_fragments import (
    GUARDRAIL_MALFORMED_REASON,
    GUARDRAIL_REJECTION_MESSAGE,
    GUARDRAIL_REJECTION_WITH_REASON,
    GUARDRAIL_UNAVAILABLE_REASON,
    build_prompt,
)
from .contracts import AgentResponse, MessageContext


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
    ) -> AgentResponse | None:
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

        if guardrail_result.allowed:
            return None

        self.logger.info(f"Guardrail blocked message: {guardrail_result.reason}")
        internal_reasons = {GUARDRAIL_UNAVAILABLE_REASON, GUARDRAIL_MALFORMED_REASON}
        reason = guardrail_result.reason
        if reason and reason not in internal_reasons:
            rejection = GUARDRAIL_REJECTION_WITH_REASON.format(reason=reason)
        else:
            rejection = GUARDRAIL_REJECTION_MESSAGE

        latency_ms = int((time.time() - start_time) * 1000)
        bt_span.log(
            output={"content": rejection, "guardrail_blocked": True},
            metrics={"latency_ms": latency_ms},
        )
        return AgentResponse(
            content=rejection,
            tool_calls=[],
            latency_ms=latency_ms,
            trace_id=bt_span.id,
        )
