"""Tests for the pricing utility."""

from chat.V2.pricing import compute_cost


class TestComputeCost:
    def test_haiku_cost(self):
        cost = compute_cost("claude-haiku-4-5-20251001", input_tokens=1000, output_tokens=100)
        assert cost is not None
        # Haiku: $0.80/M input, $4.00/M output
        expected = (1000 * 0.80 / 1_000_000) + (100 * 4.00 / 1_000_000)
        assert abs(cost - expected) < 1e-12

    def test_sonnet_cost(self):
        cost = compute_cost("claude-sonnet-4-5-20250514", input_tokens=500, output_tokens=200)
        assert cost is not None
        expected = (500 * 3.00 / 1_000_000) + (200 * 15.00 / 1_000_000)
        assert abs(cost - expected) < 1e-12

    def test_unknown_model_returns_none(self):
        cost = compute_cost("unknown-model", input_tokens=100, output_tokens=50)
        assert cost is None

    def test_zero_tokens(self):
        cost = compute_cost("claude-haiku-4-5-20251001", input_tokens=0, output_tokens=0)
        assert cost == 0.0
