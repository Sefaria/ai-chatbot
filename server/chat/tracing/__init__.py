"""
LangSmith tracing module for end-to-end observability.

Provides:
- Trace creation and management
- Span tracking for router, agent, and tools
- Metadata and tag attachment
"""

from .langsmith_tracer import (
    LangSmithTracer,
    TraceContext,
    get_tracer,
)

__all__ = [
    'LangSmithTracer',
    'TraceContext',
    'get_tracer',
]


