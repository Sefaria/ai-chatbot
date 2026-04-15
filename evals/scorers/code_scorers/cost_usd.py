"""Cost (USD) Scorer - reports the server-aggregated per-turn cost.

The eval task returns `totalCostUsd` alongside `content`. This scorer echoes
that value as the raw score so Braintrust's experiment view shows cumulative
and averaged dollar cost next to the quality scorers. The value aggregates
the main agent, guardrail, router, and summary calls (see sc-42070 / PR #112).
"""

from typing import Any

NAME = "Cost (USD)"
SLUG = "cost-usd-4201"
DESCRIPTION = (
    "Per-turn cost in USD (main agent + guardrail + router + summary). "
    "Reported as a raw dollar value; lower is better."
)


def handler(input: Any, output: Any, expected: Any, metadata: dict[str, Any]):
    # Helpers are inlined because build.py only copies the handler function
    # to the built scorer; module-level helpers are not carried along.
    cost = None
    if isinstance(output, dict):
        value = output.get("totalCostUsd")
        if isinstance(value, (int, float)):
            cost = float(value)
    if cost is None and metadata:
        value = metadata.get("totalCostUsd")
        if isinstance(value, (int, float)):
            cost = float(value)

    if cost is None:
        return {
            "score": None,
            "name": NAME,
            "metadata": {"reason": "No totalCostUsd in output or span metadata"},
        }
    return {
        "score": cost,
        "name": NAME,
        "metadata": {"cost_usd": cost},
    }
