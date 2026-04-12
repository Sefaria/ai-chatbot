"""
Pricing utility — token usage to USD conversion + per-turn cost accumulation.

Pricing data is loaded from model_pricing.json (auto-updated from LiteLLM via
GitHub Action). CostAccumulator collects auxiliary LLM costs during a turn via
a contextvars.ContextVar so services don't need to thread usage through returns.
"""

import contextvars
import json
import logging
from pathlib import Path

logger = logging.getLogger("chat.pricing")

_PRICING_PATH = Path(__file__).parent / "model_pricing.json"
_MODEL_PRICING: dict[str, dict[str, float]] = json.loads(_PRICING_PATH.read_text())


def compute_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float | None:
    """Compute USD cost from token counts and model name.

    Returns None if the model isn't in the pricing table.
    """
    pricing = _MODEL_PRICING.get(model)
    if pricing is None:
        return None
    return (input_tokens * pricing["input_cost_per_token"]) + (
        output_tokens * pricing["output_cost_per_token"]
    )


# --- Cost accumulator (per-turn context) ---

_cost_accumulator_var: contextvars.ContextVar["CostAccumulator | None"] = contextvars.ContextVar(
    "cost_accumulator", default=None
)


class CostAccumulator:
    """Collects LLM costs during a single turn. Mutable so changes are visible
    across asyncio.to_thread boundaries (ContextVar copies the reference)."""

    def __init__(self) -> None:
        self._total: float = 0.0

    def add(self, model: str, input_tokens: int, output_tokens: int) -> None:
        cost = compute_cost(model, input_tokens, output_tokens)
        if cost is not None:
            self._total += cost
        elif input_tokens > 0:
            logger.warning(f"No pricing for model: {model}")

    @property
    def total(self) -> float:
        return self._total


def init_cost_accumulator() -> CostAccumulator:
    """Create a fresh accumulator and store it in the current context."""
    acc = CostAccumulator()
    _cost_accumulator_var.set(acc)
    return acc


def get_cost_accumulator() -> CostAccumulator | None:
    """Retrieve the current turn's accumulator, or None if not initialized."""
    return _cost_accumulator_var.get()
