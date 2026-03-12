"""Shared contracts for the V2 Claude agent runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class AgentProgressUpdate:
    """Streamed to the client via SSE during a single chat turn."""

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
    """Per-request context passed through the agent layer for prompting and tracing."""

    summary_text: str | None = None
    page_url: str | None = None
    session_id: str | None = None
    origin: str | None = None


@dataclass
class AgentResponse:
    """Returned by send_message(); consumed by views + logging layers."""

    content: str
    tool_calls: list[dict[str, Any]]
    latency_ms: int
    model: str | None = None
    trace_id: str | None = None
    llm_calls: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cache_read_tokens: int | None = None
    total_cost_usd: float | None = None


ProgressCallback = Callable[[AgentProgressUpdate], None]


class ProgressSink(Protocol):
    def emit(self, update: AgentProgressUpdate) -> None: ...


class GuardrailGate(Protocol):
    async def run_guardrail(
        self, bt_span: Any, user_message: str, context: MessageContext, start_time: float
    ) -> AgentResponse | None: ...


class SdkRunner(Protocol):
    async def run(self, options: Any, prompt_text: str) -> Any: ...


class TraceLogger(Protocol):
    def log_input(
        self, bt_span: Any, user_message: str, context: MessageContext, model: str
    ) -> None: ...
