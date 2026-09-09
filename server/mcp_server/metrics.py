"""Prometheus metrics for the public MCP server.

Metric names, labels and buckets are kept identical to the standalone
``sefaria-mcp`` server so existing Grafana dashboards keep working across the
cutover. Do not rename these without migrating the dashboards.
"""

import logging
import os

from prometheus_client import Counter, Histogram, start_http_server

logger = logging.getLogger("mcp_server.metrics")

DEFAULT_METRICS_PORT = 9090

tool_calls_total = Counter(
    "mcp_tool_calls_total",
    "Total number of MCP tool calls",
    ["tool_name", "status"],
)

tool_duration_seconds = Histogram(
    "mcp_tool_duration_seconds",
    "Duration of MCP tool calls in seconds",
    ["tool_name"],
)

tool_payload_bytes = Histogram(
    "mcp_tool_payload_bytes",
    "Size of MCP tool response payloads in bytes",
    ["tool_name"],
    buckets=[100, 1000, 10000, 100000, 1000000, 10000000],
)

errors_total = Counter(
    "mcp_errors_total",
    "Total number of MCP errors",
    ["tool_name", "error_type"],
)


def metrics_port() -> int:
    return int(os.environ.get("SEFARIA_MCP_METRICS_PORT", DEFAULT_METRICS_PORT))


def start_metrics_server(port: int | None = None) -> None:
    """Start the Prometheus endpoint on its own port, never fatally."""
    port = port if port is not None else metrics_port()
    try:
        start_http_server(port)
        logger.info("Prometheus metrics listening on :%s", port)
    except OSError as exc:
        logger.warning("Skipping metrics server on port %s: %s", port, exc)
