"""Observability module for tracing and metrics.

This abstraction decouples application code from specific tracing providers
(Braintrust, database, etc.), enabling dual-logging and provider swapping.

Usage:
    from chat.observability import start_span, create_span, current_span, traced

    # Context manager for scoped spans (most common)
    with start_span(name="my-operation", type="llm") as span:
        span.log(input={"query": "..."})
        # do work
        span.log(output={...}, metrics={"latency_ms": 100})

    # Manual lifecycle for cross-function spans (e.g., orchestrator)
    span = create_span(name="request", type="task")
    span.log(input={...})
    # ... later in another function ...
    span.log(output={...})
    span.end()  # Must call manually

    # Decorator for automatic function tracing
    @traced(name="my_function", type="function")
    def my_function():
        span = current_span()  # Access current span
        span.log(metadata={"key": "value"})

Span types: task, llm, function, tool

Backends:
- BraintrustBackend: Sends to Braintrust (active when BRAINTRUST_API_KEY set)
- DatabaseBackend: (Future) Logs to local database
"""

from collections.abc import Generator
from contextlib import contextmanager

from .backends import BraintrustBackend
from .tracer import Span, SpanData, Tracer
from .tracer import create_span as _create_span
from .tracer import current_span as _current_span
from .tracer import traced as _traced

# Global tracer singleton
_tracer: Tracer | None = None


def get_tracer() -> Tracer:
    """Get the global tracer instance.

    Creates a tracer with BraintrustBackend on first call.
    Returns the same instance on subsequent calls.
    """
    global _tracer
    if _tracer is None:
        _tracer = Tracer(backends=[BraintrustBackend()])
    return _tracer


def _reset_tracer() -> None:
    """Reset the global tracer singleton (for testing)."""
    global _tracer
    _tracer = None


@contextmanager
def start_span(name: str, type: str) -> Generator[Span, None, None]:  # noqa: A002
    """Start a span using the global tracer.

    This is a convenience function that uses the global tracer's backends.

    Args:
        name: Human-readable span name
        type: Span type (task, llm, function, tool)

    Yields:
        The created Span instance
    """
    tracer = get_tracer()
    with tracer.start_span(name=name, type=type) as span:
        yield span


def current_span() -> Span | None:
    """Get the currently active span."""
    return _current_span()


def create_span(name: str, type: str) -> Span:  # noqa: A002
    """Create a span for manual lifecycle management.

    Unlike start_span (context manager), this returns a span that you
    manage manually. Call span.end() when done.

    Uses the global tracer's backends.

    Args:
        name: Human-readable span name
        type: Span type (task, llm, function, tool)

    Returns:
        The created Span instance
    """
    tracer = get_tracer()
    return _create_span(name=name, type=type, backends=tracer.backends)


def traced(name: str, type: str):  # noqa: A002
    """Decorator to create spans around functions.

    Uses the global tracer's backends.
    """
    return _traced(name=name, type=type)


__all__ = [
    "BraintrustBackend",
    "Span",
    "SpanData",
    "Tracer",
    "_reset_tracer",
    "create_span",
    "current_span",
    "get_tracer",
    "start_span",
    "traced",
]
