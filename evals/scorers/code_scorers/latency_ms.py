"""Latency (ms) Scorer - reports the server-measured per-turn wall time.

The eval task returns `latencyMs` alongside `content`. This scorer echoes
that value as the raw score so Braintrust aggregates wall time the same way
it does quality scores. Using the server-reported number keeps comparisons
between experiments apples-to-apples regardless of network jitter between the
eval runner and the server.
"""

from typing import Any

NAME = "Latency (ms)"
SLUG = "latency-ms-4202"
DESCRIPTION = (
    "Per-turn server-side latency in milliseconds. Reported as a raw number; "
    "lower is better."
)


def handler(input: Any, output: Any, expected: Any, metadata: dict[str, Any]):
    # Helpers are inlined because build.py only copies the handler function
    # to the built scorer; module-level helpers are not carried along.
    latency = None
    if isinstance(output, dict):
        value = output.get("latencyMs")
        if isinstance(value, (int, float)):
            latency = int(value)
    if latency is None and metadata:
        value = metadata.get("latencyMs")
        if isinstance(value, (int, float)):
            latency = int(value)

    if latency is None:
        return {
            "score": None,
            "name": NAME,
            "metadata": {"reason": "No latencyMs in output or span metadata"},
        }
    return {
        "score": latency,
        "name": NAME,
        "metadata": {"latency_ms": latency},
    }
