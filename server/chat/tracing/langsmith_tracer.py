"""
LangSmith tracer for comprehensive observability.

Creates hierarchical traces with spans for:
- Router decisions
- Prompt fetching
- Claude agent runs
- Individual tool calls
- Summary updates
"""

import logging
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger("chat.tracing")


@dataclass
class SpanData:
    """Data for a trace span."""

    span_id: str
    name: str
    span_type: str  # 'chain', 'llm', 'tool', 'retriever'
    start_time: float
    end_time: float | None = None
    inputs: dict[str, Any] | None = None
    outputs: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    parent_span_id: str | None = None


@dataclass
class TraceContext:
    """Context for a trace (parent trace for a turn)."""

    run_id: str
    session_id: str
    turn_id: str
    user_id: str | None = None
    flow: str | None = None
    decision_id: str | None = None
    start_time: float = field(default_factory=time.time)
    spans: list[SpanData] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


class LangSmithTracer:
    """
    LangSmith tracer for the Jewish learning agent.

    Provides:
    - Automatic trace creation per turn
    - Span management for nested operations
    - Structured logging with metadata
    - Error tracking
    """

    def __init__(
        self,
        api_key: str | None = None,
        project_name: str | None = None,
        endpoint: str | None = None,
    ):
        """
        Initialize the LangSmith tracer.

        Args:
            api_key: LangSmith API key (default: from env)
            project_name: LangSmith project name (default: from env)
            endpoint: LangSmith API endpoint (default: from env or cloud)
        """
        self.api_key = api_key or os.environ.get("LANGSMITH_API_KEY")
        self.project_name = project_name or os.environ.get("LANGSMITH_PROJECT", "sefaria-chatbot")
        self.endpoint = endpoint or os.environ.get(
            "LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"
        )

        self._client = None
        self._enabled = False
        self._current_traces: dict[str, TraceContext] = {}

        self._init_client()

    def _init_client(self):
        """Initialize the LangSmith client."""
        if not self.api_key:
            logger.info("LangSmith API key not configured, tracing disabled")
            return

        try:
            from langsmith import Client

            self._client = Client(
                api_key=self.api_key,
                api_url=self.endpoint,
            )
            self._enabled = True
            logger.info(f"LangSmith tracing enabled for project: {self.project_name}")
        except ImportError:
            logger.warning("langsmith package not installed, tracing disabled")
        except Exception as e:
            logger.warning(f"Failed to initialize LangSmith: {e}")

    @property
    def enabled(self) -> bool:
        """Check if tracing is enabled."""
        return self._enabled

    def create_trace(
        self,
        session_id: str,
        turn_id: str,
        user_id: str | None = None,
        flow: str | None = None,
        decision_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> TraceContext:
        """
        Create a new trace for a turn.

        Args:
            session_id: Session identifier
            turn_id: Turn identifier
            user_id: User identifier
            flow: Flow type (HALACHIC, GENERAL, SEARCH)
            decision_id: Route decision ID
            metadata: Additional metadata
            tags: Tags for filtering

        Returns:
            TraceContext for this turn
        """
        run_id = str(uuid.uuid4())

        context = TraceContext(
            run_id=run_id,
            session_id=session_id,
            turn_id=turn_id,
            user_id=user_id,
            flow=flow,
            decision_id=decision_id,
            metadata=metadata or {},
            tags=tags or ["sefaria-agent"],
        )

        self._current_traces[turn_id] = context

        if self._enabled and self._client:
            try:
                # Create the parent run in LangSmith
                self._client.create_run(
                    name="chat_turn",
                    run_type="chain",
                    project_name=self.project_name,
                    id=run_id,
                    inputs={"turn_id": turn_id, "session_id": session_id},
                    extra={
                        "metadata": {
                            "user_id": user_id,
                            "flow": flow,
                            "decision_id": decision_id,
                            **(metadata or {}),
                        },
                        "tags": context.tags,
                    },
                )
            except Exception as e:
                logger.warning(f"Failed to create LangSmith run: {e}")

        return context

    @contextmanager
    def span(
        self,
        context: TraceContext,
        name: str,
        span_type: str = "chain",
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        """
        Create a span within a trace.

        Args:
            context: Parent trace context
            name: Span name
            span_type: Type of span (chain, llm, tool, retriever)
            inputs: Input data
            metadata: Additional metadata

        Yields:
            SpanData that can be updated with outputs
        """
        span_id = str(uuid.uuid4())
        span = SpanData(
            span_id=span_id,
            name=name,
            span_type=span_type,
            start_time=time.time(),
            inputs=inputs,
            metadata=metadata or {},
            parent_span_id=context.run_id,
        )

        context.spans.append(span)

        if self._enabled and self._client:
            try:
                self._client.create_run(
                    name=name,
                    run_type=span_type,
                    project_name=self.project_name,
                    id=span_id,
                    parent_run_id=context.run_id,
                    inputs=inputs or {},
                    extra={"metadata": metadata or {}},
                )
            except Exception as e:
                logger.warning(f"Failed to create LangSmith span: {e}")

        try:
            yield span
        except Exception as e:
            span.error = str(e)
            raise
        finally:
            span.end_time = time.time()

            if self._enabled and self._client:
                try:
                    self._client.update_run(
                        run_id=span_id,
                        outputs=span.outputs or {},
                        error=span.error,
                        end_time=datetime.now(),
                    )
                except Exception as e:
                    logger.warning(f"Failed to update LangSmith span: {e}")

    def log_router_decision(
        self,
        context: TraceContext,
        user_message: str,
        decision: dict[str, Any],
        latency_ms: int,
    ):
        """Log a router decision as a span."""
        with self.span(
            context,
            name="router",
            span_type="chain",
            inputs={"user_message": user_message[:500]},
            metadata={"latency_ms": latency_ms},
        ) as span:
            span.outputs = decision

    def log_prompt_fetch(
        self,
        context: TraceContext,
        prompt_ids: dict[str, str],
        prompt_versions: dict[str, str],
        latency_ms: int,
    ):
        """Log prompt fetching as a span."""
        with self.span(
            context,
            name="prompt_fetch",
            span_type="retriever",
            inputs={"prompt_ids": prompt_ids},
            metadata={"latency_ms": latency_ms},
        ) as span:
            span.outputs = {"versions": prompt_versions}

    def log_llm_call(
        self,
        context: TraceContext,
        model: str,
        messages: list[dict[str, Any]],
        response_content: str,
        usage: dict[str, int],
        latency_ms: int,
        iteration: int = 1,
    ):
        """Log an LLM call as a generation span."""
        with self.span(
            context,
            name=f"claude_completion_{iteration}",
            span_type="llm",
            inputs={"messages": messages, "model": model},
            metadata={
                "latency_ms": latency_ms,
                "iteration": iteration,
                "model": model,
            },
        ) as span:
            span.outputs = {
                "content": response_content[:2000],
                "usage": usage,
            }

    def log_tool_call(
        self,
        context: TraceContext,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_output: Any,
        latency_ms: int,
        is_error: bool = False,
        error_message: str | None = None,
    ):
        """Log a tool call as a span."""
        with self.span(
            context,
            name=f"tool_{tool_name}",
            span_type="tool",
            inputs={"tool_name": tool_name, "input": tool_input},
            metadata={
                "latency_ms": latency_ms,
                "is_error": is_error,
            },
        ) as span:
            if is_error:
                span.error = error_message
            span.outputs = {"output": str(tool_output)[:2000] if tool_output else None}

    def log_summary_update(
        self,
        context: TraceContext,
        old_summary: str,
        new_summary: str,
        latency_ms: int,
    ):
        """Log a summary update as a span."""
        with self.span(
            context,
            name="summary_update",
            span_type="chain",
            inputs={"old_summary": old_summary[:500]},
            metadata={"latency_ms": latency_ms},
        ) as span:
            span.outputs = {"new_summary": new_summary[:500]}

    def end_trace(
        self,
        context: TraceContext,
        output: str,
        error: str | None = None,
        metrics: dict[str, Any] | None = None,
    ):
        """
        End a trace and flush to LangSmith.

        Args:
            context: Trace context
            output: Final output
            error: Error message if failed
            metrics: Performance metrics
        """
        if self._enabled and self._client:
            try:
                self._client.update_run(
                    run_id=context.run_id,
                    outputs={"response": output[:2000]},
                    error=error,
                    end_time=datetime.now(),
                    extra={
                        "metadata": {
                            **context.metadata,
                            **(metrics or {}),
                        },
                    },
                )
            except Exception as e:
                logger.warning(f"Failed to end LangSmith run: {e}")

        # Clean up
        if context.turn_id in self._current_traces:
            del self._current_traces[context.turn_id]

    def get_trace_url(self, context: TraceContext) -> str | None:
        """Get the LangSmith URL for a trace."""
        if not self._enabled:
            return None

        return f"{self.endpoint.replace('api.', '')}/o/default/projects/{self.project_name}/r/{context.run_id}"


# Default tracer instance
_default_tracer = None


def get_tracer() -> LangSmithTracer:
    """Get or create the default tracer."""
    global _default_tracer
    if _default_tracer is None:
        _default_tracer = LangSmithTracer()
    return _default_tracer
