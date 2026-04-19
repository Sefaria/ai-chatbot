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
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float | None:
    """Compute USD cost from token counts and model name.

    `input_tokens` is the non-cached input count (Anthropic reports cached
    tokens separately in `cache_creation_input_tokens` / `cache_read_input_tokens`).
    Cache token costs contribute 0 if the pricing entry lacks cache fields.

    Returns None if the model isn't in the pricing table.
    """
    pricing = _MODEL_PRICING.get(model)
    if pricing is None:
        return None
    return (
        (input_tokens * pricing["input_cost_per_token"])
        + (output_tokens * pricing["output_cost_per_token"])
        + (cache_creation_tokens * pricing.get("cache_creation_input_token_cost", 0))
        + (cache_read_tokens * pricing.get("cache_read_input_token_cost", 0))
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

    def add(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_creation_tokens: int = 0,
        cache_read_tokens: int = 0,
    ) -> None:
        cost = compute_cost(
            model,
            input_tokens,
            output_tokens,
            cache_creation_tokens,
            cache_read_tokens,
        )
        if cost is not None:
            self._total += cost
        elif (
            input_tokens > 0
            or output_tokens > 0
            or cache_creation_tokens > 0
            or cache_read_tokens > 0
        ):
            logger.warning(f"No pricing for model: {model}")

    def add_from_response(self, model: str, response) -> None:
        """Add cost from an Anthropic Messages API response.

        Pulls input/output and cache token counts from `response.usage`. Cache
        fields are Optional[int] in the SDK (None when the model doesn't use
        prompt caching), so we normalize to 0.
        """
        usage = response.usage
        self.add(
            model,
            usage.input_tokens,
            usage.output_tokens,
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", None) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", None) or 0,
        )

    @property
    def total(self) -> float:
        return self._total


def init_cost_accumulator() -> CostAccumulator:
    """Create a fresh accumulator and store it in the current context.

    Pair with reset_cost_accumulator() in a try/finally to avoid leaking the
    reference across requests on reused worker threads.
    """
    acc = CostAccumulator()
    _cost_accumulator_var.set(acc)
    return acc


def bind_cost_accumulator(accumulator: CostAccumulator) -> None:
    """Bind an existing accumulator into the current context.

    For threads started without `contextvars.copy_context()` — the accumulator
    is captured by closure but the ContextVar itself isn't. Call this from
    the new thread so guardrail/router's `get_cost_accumulator()` resolves.
    """
    _cost_accumulator_var.set(accumulator)


def reset_cost_accumulator() -> None:
    """Clear the accumulator from the current context."""
    _cost_accumulator_var.set(None)


def get_cost_accumulator() -> CostAccumulator | None:
    """Retrieve the current turn's accumulator, or None if not initialized."""
    return _cost_accumulator_var.get()
