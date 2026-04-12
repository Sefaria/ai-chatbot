"""
Pricing utility — converts Anthropic API token usage into USD cost estimates.

Used to track costs of auxiliary LLM calls (guardrail, router, summary) that
happen outside the Claude Agent SDK, which tracks its own costs internally.
"""

# Per-token prices in USD. Only models used by auxiliary services need entries.
# Source: https://docs.anthropic.com/en/docs/about-claude/models
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5-20251001": {
        "input": 0.80 / 1_000_000,
        "output": 4.00 / 1_000_000,
    },
    "claude-sonnet-4-5-20250514": {
        "input": 3.00 / 1_000_000,
        "output": 15.00 / 1_000_000,
    },
}


def compute_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float | None:
    """Compute USD cost from token counts and model name.

    Returns None if the model isn't in the pricing table (caller should log a
    warning but not fail — missing cost is better than a crash).
    """
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        return None
    return (input_tokens * pricing["input"]) + (output_tokens * pricing["output"])
