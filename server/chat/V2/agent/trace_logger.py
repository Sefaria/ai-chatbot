"""Braintrust span logging helpers for agent turns."""

from __future__ import annotations

from typing import Any

from .contracts import MessageContext
from .helpers import extract_refs


class BraintrustTraceLogger:
    """Builds and emits Braintrust span log payloads."""

    def log_input(
        self,
        *,
        bt_span: Any,
        user_message: str,
        context: MessageContext,
        model: str,
    ) -> None:
        span_input: dict[str, Any] = {"message": user_message}
        if context.page_url:
            span_input["page_url"] = context.page_url
        if context.summary_text:
            span_input["summary"] = context.summary_text
        span_metadata: dict[str, Any] = {"model": model}
        if context.session_id:
            span_metadata["session_id"] = context.session_id
        bt_span.log(input=span_input, metadata=span_metadata)

    def log_prompt_metadata(
        self,
        *,
        bt_span: Any,
        core_prompt_id: str,
        core_prompt_version: str,
        system_prompt_in_options: bool,
        summary_included: bool,
    ) -> None:
        bt_span.log(
            metadata={
                "core_prompt_id": core_prompt_id,
                "core_prompt_version": core_prompt_version,
                "core_prompt_in_options": system_prompt_in_options,
                "summary_included": summary_included,
            }
        )

    def log_error(self, *, bt_span: Any, exc: Exception, latency_ms: int) -> None:
        bt_span.log(
            output=str(exc),
            metrics={"latency_ms": latency_ms},
            metadata={"status": "error", "error": str(exc)},
        )

    def log_success(
        self,
        *,
        bt_span: Any,
        content: str,
        tool_calls: list[dict[str, Any]],
        metrics: dict[str, Any],
    ) -> None:
        refs = extract_refs(tool_calls)
        span_output: dict[str, Any] = {
            "content": content,
            "ref_count": len(refs),
            "tool_count": len(tool_calls),
        }
        span_metadata: dict[str, Any] = {
            "refs": refs,
            "tool_calls": tool_calls,
        }
        bt_span.log(output=span_output, metrics=metrics, metadata=span_metadata)

