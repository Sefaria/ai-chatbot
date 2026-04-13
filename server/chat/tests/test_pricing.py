"""Tests for the pricing utility."""

from chat.V2.pricing import (
    CostAccumulator,
    compute_cost,
    get_cost_accumulator,
    init_cost_accumulator,
    reset_cost_accumulator,
)


class TestComputeCost:
    def test_haiku_cost(self):
        cost = compute_cost("claude-haiku-4-5-20251001", input_tokens=1000, output_tokens=100)
        assert cost is not None
        # Haiku: $1.00/M input, $5.00/M output (from LiteLLM JSON)
        expected = (1000 * 1e-06) + (100 * 5e-06)
        assert abs(cost - expected) < 1e-12

    def test_openai_model(self):
        cost = compute_cost("gpt-4o", input_tokens=1000, output_tokens=500)
        assert cost is not None
        assert cost > 0

    def test_unknown_model_returns_none(self):
        cost = compute_cost("unknown-model", input_tokens=100, output_tokens=50)
        assert cost is None

    def test_zero_tokens(self):
        cost = compute_cost("claude-haiku-4-5-20251001", input_tokens=0, output_tokens=0)
        assert cost == 0.0

    def test_cache_tokens_included(self):
        cost = compute_cost(
            "claude-haiku-4-5-20251001",
            input_tokens=1000,
            output_tokens=100,
            cache_creation_tokens=500,
            cache_read_tokens=2000,
        )
        # Haiku: input $1.00/M, output $5.00/M, cache write $1.25/M, cache read $0.10/M
        expected = (1000 * 1e-06) + (100 * 5e-06) + (500 * 1.25e-06) + (2000 * 1e-07)
        assert abs(cost - expected) < 1e-12

    def test_cache_tokens_default_to_zero(self):
        without = compute_cost("claude-haiku-4-5-20251001", input_tokens=1000, output_tokens=100)
        with_zero_cache = compute_cost(
            "claude-haiku-4-5-20251001",
            input_tokens=1000,
            output_tokens=100,
            cache_creation_tokens=0,
            cache_read_tokens=0,
        )
        assert without == with_zero_cache


class TestCostAccumulator:
    def test_starts_at_zero(self):
        acc = CostAccumulator()
        assert acc.total == 0.0

    def test_add_known_model(self):
        acc = CostAccumulator()
        acc.add("claude-haiku-4-5-20251001", input_tokens=1000, output_tokens=100)
        assert acc.total > 0

    def test_add_unknown_model_ignored(self):
        acc = CostAccumulator()
        acc.add("unknown-model", input_tokens=1000, output_tokens=100)
        assert acc.total == 0.0

    def test_add_with_cache_tokens(self):
        acc = CostAccumulator()
        acc.add(
            "claude-haiku-4-5-20251001",
            input_tokens=1000,
            output_tokens=100,
            cache_creation_tokens=500,
            cache_read_tokens=2000,
        )
        expected = (1000 * 1e-06) + (100 * 5e-06) + (500 * 1.25e-06) + (2000 * 1e-07)
        assert abs(acc.total - expected) < 1e-12

    def test_accumulates_multiple_calls(self):
        acc = CostAccumulator()
        acc.add("claude-haiku-4-5-20251001", input_tokens=1000, output_tokens=100)
        first = acc.total
        acc.add("claude-haiku-4-5-20251001", input_tokens=1000, output_tokens=100)
        assert acc.total == first * 2

    def test_context_var_lifecycle(self):
        # Reset first — other tests (or prior requests on a reused WSGI thread)
        # may have left a stale accumulator on the ContextVar.
        reset_cost_accumulator()
        assert get_cost_accumulator() is None
        acc = init_cost_accumulator()
        assert get_cost_accumulator() is acc
        acc.add("claude-haiku-4-5-20251001", input_tokens=500, output_tokens=50)
        assert get_cost_accumulator().total == acc.total
        reset_cost_accumulator()
        assert get_cost_accumulator() is None
