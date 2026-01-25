"""
Tracing backends for the observability module.

Available backends:
- BraintrustBackend: Wraps the braintrust SDK for cloud tracing
- (Future) DatabaseBackend: Logs spans to the database

Usage:
    from chat.observability.backends import BraintrustBackend
    from chat.observability.tracer import Tracer

    tracer = Tracer(backends=[BraintrustBackend()])
    with tracer.start_span(name="request", type="task") as span:
        span.log(input={...})
"""

import os
from typing import Any

import braintrust

__all__ = ["BraintrustBackend"]


class BraintrustBackend:
    """Backend that sends spans to Braintrust.

    Wraps the braintrust SDK to provide compatibility with our tracer abstraction.
    Automatically disabled when BRAINTRUST_API_KEY is not set.
    """

    def __init__(self) -> None:
        self.enabled = self._has_api_key()

    def _has_api_key(self) -> bool:
        """Check if Braintrust API key is configured."""
        return bool(os.environ.get("BRAINTRUST_API_KEY"))

    def record_span(self, span_data: dict[str, Any]) -> None:
        """Record a span to Braintrust.

        Creates a braintrust span and logs all accumulated data.

        Args:
            span_data: Dictionary with span data from Span.to_dict()
        """
        if not self.enabled:
            return

        name = span_data.get("name", "unknown")
        span_type = span_data.get("type", "task")

        # Create braintrust span and log data
        with braintrust.start_span(name=name, type=span_type) as bt_span:
            bt_span.log(
                input=span_data.get("input"),
                output=span_data.get("output"),
                metadata=span_data.get("metadata") or {},
                metrics=span_data.get("metrics") or {},
                tags=span_data.get("tags") or [],
                error=span_data.get("error"),
            )
