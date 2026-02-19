"""
Prometheus metrics for chatbot agent and tool usage.

Enables time-series queries (e.g. calls per tool per hour) in Grafana.
See docs/plans/metrics-for-chatbot-agent-tools.md.
"""

from prometheus_client import Counter, Histogram

# Per-tool metrics (SC-41316)
TOOL_CALLS = Counter(
    "chatbot_tool_calls_total",
    "Total tool invocations",
    ["tool_name", "status"],
)
TOOL_DURATION = Histogram(
    "chatbot_tool_duration_seconds",
    "Tool execution latency in seconds",
    ["tool_name"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
TOOL_ERRORS = Counter(
    "chatbot_tool_errors_total",
    "Tool execution errors",
    ["tool_name"],
)


def record_tool_call(tool_name: str, status: str, duration_seconds: float) -> None:
    """Record a tool invocation for Prometheus."""
    TOOL_CALLS.labels(tool_name=tool_name, status=status).inc()
    TOOL_DURATION.labels(tool_name=tool_name).observe(duration_seconds)
    if status == "error":
        TOOL_ERRORS.labels(tool_name=tool_name).inc()
