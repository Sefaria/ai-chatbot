"""
Provider-agnostic tracing abstraction for observability.

This module provides a tracing interface that:
1. Captures all data currently sent to Braintrust
2. Supports multiple backends (Braintrust, database, etc.)
3. Uses contextvars for proper span context propagation
4. Provides @traced decorator and start_span context manager

Usage:
    from chat.observability.tracer import start_span, current_span, traced

    # Context manager
    with start_span(name="request", type="task") as span:
        span.log(input={"query": "..."})
        # do work
        span.log(output={...}, metrics={...})

    # Decorator
    @traced(name="my_function", type="function")
    def my_function():
        span = current_span()
        span.log(input={...})
        return result
"""

import asyncio
import contextvars
import time
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Protocol

# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class SpanData:
    """Accumulated data from span.log() calls.

    Supports merging from multiple calls - input at start, output/metrics at end.
    """

    input: Any = None
    output: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    error: str | None = None

    def merge(
        self,
        input: Any = None,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        error: str | None = None,
    ) -> None:
        """Merge new data into accumulated span data."""
        if input is not None:
            self.input = input
        if output is not None:
            self.output = output
        if metadata:
            self.metadata.update(metadata)
        if metrics:
            self.metrics.update(metrics)
        if tags:
            for tag in tags:
                if tag not in self.tags:
                    self.tags.append(tag)
        if error is not None:
            self.error = error


# =============================================================================
# Backend Protocol
# =============================================================================


class TracingBackend(Protocol):
    """Protocol for tracing backends that receive span data."""

    def record_span(self, span_data: dict[str, Any]) -> None:
        """Record a completed span."""
        ...


# =============================================================================
# Span
# =============================================================================


class Span:
    """A tracing span that captures observability data.

    Attributes:
        span_id: Unique identifier for this span
        trace_id: Shared identifier for all spans in a trace
        parent_id: ID of parent span (None for root spans)
        name: Human-readable span name
        span_type: Type of span (task, llm, function, tool)
        start_time: Unix timestamp when span started
        duration_ms: Duration in milliseconds (set on end)
        data: Accumulated SpanData from log() calls
    """

    def __init__(
        self,
        name: str,
        span_type: str,
        trace_id: str | None = None,
        parent_id: str | None = None,
        backends: list[TracingBackend] | None = None,
        context_token: contextvars.Token | None = None,
    ):
        self.span_id = uuid.uuid4().hex
        self.trace_id = trace_id or self.span_id  # Root span: trace_id = span_id
        self.parent_id = parent_id
        self.name = name
        self.span_type = span_type
        self.start_time = time.time()
        self.duration_ms: int | None = None
        self.data = SpanData()
        self._backends = backends or []
        self._ended = False
        self._context_token = context_token  # For resetting current_span on end()

    def log(
        self,
        input: Any = None,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        error: str | None = None,
    ) -> None:
        """Log data to the span.

        Can be called multiple times - data is accumulated/merged.
        """
        self.data.merge(
            input=input,
            output=output,
            metadata=metadata,
            metrics=metrics,
            tags=tags,
            error=error,
        )

    def end(self) -> None:
        """End the span and record to backends."""
        if self._ended:
            return

        self._ended = True
        self.duration_ms = int((time.time() - self.start_time) * 1000)

        # Reset context if we have a token (from create_span)
        if self._context_token is not None:
            _current_span.reset(self._context_token)

        # Record to all backends
        span_dict = self.to_dict()
        for backend in self._backends:
            try:
                backend.record_span(span_dict)
            except Exception:
                pass  # Don't fail span on backend errors

    def to_dict(self) -> dict[str, Any]:
        """Export span data as dictionary for storage/transmission."""
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "type": self.span_type,
            "start_time": self.start_time,
            "duration_ms": self.duration_ms,
            "input": self.data.input,
            "output": self.data.output,
            "metadata": self.data.metadata,
            "metrics": self.data.metrics,
            "tags": self.data.tags,
            "error": self.data.error,
        }


# =============================================================================
# Context Management
# =============================================================================

# Context variable to track current span
_current_span: contextvars.ContextVar[Span | None] = contextvars.ContextVar(
    "current_span", default=None
)


def current_span() -> Span | None:
    """Get the currently active span, or None if not in a span context."""
    return _current_span.get()


def create_span(
    name: str,
    type: str,  # noqa: A002 - matches Braintrust API
    backends: list[TracingBackend] | None = None,
) -> Span:
    """Create a span for manual lifecycle management.

    Unlike start_span (context manager), this returns a span that you
    manage manually. Call span.end() when done.

    This is useful for spans that cross function boundaries, like the
    request span created in prepare_turn() and ended in views.py.

    Args:
        name: Human-readable name for the span
        type: Type of span (task, llm, function, tool)
        backends: Optional list of backends to record to

    Returns:
        The created Span instance

    Example:
        span = create_span(name="request", type="task")
        span.log(input={"query": "..."})
        # ... later ...
        span.log(output={...})
        span.end()  # Records to backends and restores previous current_span
    """
    parent = _current_span.get()

    span = Span(
        name=name,
        span_type=type,
        trace_id=parent.trace_id if parent else None,
        parent_id=parent.span_id if parent else None,
        backends=backends or [],
    )

    # Set as current and store token for reset on end()
    token = _current_span.set(span)
    span._context_token = token

    return span


@contextmanager
def start_span(
    name: str,
    type: str,  # noqa: A002 - matches Braintrust API
    backends: list[TracingBackend] | None = None,
) -> Generator[Span, None, None]:
    """Start a new span as a context manager.

    Args:
        name: Human-readable name for the span
        type: Type of span (task, llm, function, tool)
        backends: Optional list of backends to record to

    Yields:
        The created Span instance

    Example:
        with start_span(name="request", type="task") as span:
            span.log(input={"query": "..."})
            # do work
            span.log(output={...})
    """
    parent = _current_span.get()

    span = Span(
        name=name,
        span_type=type,
        trace_id=parent.trace_id if parent else None,
        parent_id=parent.span_id if parent else None,
        backends=backends or [],
    )

    token = _current_span.set(span)
    try:
        yield span
    finally:
        _current_span.reset(token)
        span.end()


def traced(
    name: str,
    type: str,  # noqa: A002 - matches Braintrust API
) -> Callable:
    """Decorator to automatically create a span around a function.

    Works with both sync and async functions.

    Args:
        name: Span name
        type: Span type (task, llm, function, tool)

    Example:
        @traced(name="my_function", type="function")
        def my_function():
            span = current_span()
            span.log(input={...})
            return result
    """

    def decorator(func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                with start_span(name=name, type=type):
                    return await func(*args, **kwargs)

            return async_wrapper
        else:

            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                with start_span(name=name, type=type):
                    return func(*args, **kwargs)

            return sync_wrapper

    return decorator


# =============================================================================
# Tracer Class (for custom backend configuration)
# =============================================================================


class Tracer:
    """Tracer with configured backends.

    Use this when you need to specify custom backends.

    Example:
        tracer = Tracer(backends=[BraintrustBackend(), DatabaseBackend()])
        with tracer.start_span(name="request", type="task") as span:
            span.log(input={...})
    """

    def __init__(self, backends: list[TracingBackend] | None = None):
        self.backends = backends or []

    @contextmanager
    def start_span(
        self,
        name: str,
        type: str,  # noqa: A002
    ) -> Generator[Span, None, None]:
        """Start a span with this tracer's backends."""
        parent = _current_span.get()

        span = Span(
            name=name,
            span_type=type,
            trace_id=parent.trace_id if parent else None,
            parent_id=parent.span_id if parent else None,
            backends=self.backends,
        )

        token = _current_span.set(span)
        try:
            yield span
        finally:
            _current_span.reset(token)
            span.end()
