"""Metrics and usage normalization helpers for agent turns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import AgentResponse


@dataclass
class UsageMetrics:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cache_read_tokens: int | None = None

    @property
    def prompt_tokens(self) -> int | None:
        if self.input_tokens is None:
            return None
        return self.input_tokens + (self.cache_read_tokens or 0) + (self.cache_creation_tokens or 0)


def map_usage(usage: dict[str, Any] | None) -> UsageMetrics:
    if not usage:
        return UsageMetrics()
    return UsageMetrics(
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        cache_creation_tokens=usage.get("cache_creation_input_tokens"),
        cache_read_tokens=usage.get("cache_read_input_tokens"),
    )


def build_braintrust_metrics(
    *,
    latency_ms: int,
    tool_count: int,
    llm_call_count: int,
    usage: UsageMetrics,
    total_cost_usd: float | None,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "latency_ms": latency_ms,
        "tool_count": tool_count,
    }
    if llm_call_count:
        metrics["llm_calls"] = llm_call_count
    if usage.prompt_tokens is not None:
        metrics["prompt_tokens"] = usage.prompt_tokens
        metrics["completion_tokens"] = usage.output_tokens or 0
        metrics["tokens"] = usage.prompt_tokens + (usage.output_tokens or 0)
    if usage.cache_read_tokens is not None:
        metrics["prompt_cached_tokens"] = usage.cache_read_tokens
    if usage.cache_creation_tokens is not None:
        metrics["prompt_cache_creation_tokens"] = usage.cache_creation_tokens
    if total_cost_usd is not None:
        metrics["total_cost_usd"] = total_cost_usd
    return metrics


def build_agent_response(
    *,
    content: str,
    tool_calls: list[dict[str, Any]],
    latency_ms: int,
    model: str,
    trace_id: str,
    llm_call_count: int,
    usage: UsageMetrics,
    total_cost_usd: float | None,
) -> AgentResponse:
    return AgentResponse(
        content=content,
        tool_calls=tool_calls,
        latency_ms=latency_ms,
        model=model,
        trace_id=trace_id,
        llm_calls=llm_call_count or None,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_tokens=usage.cache_creation_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        total_cost_usd=total_cost_usd,
    )
