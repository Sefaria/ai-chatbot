"""Tests for token usage metrics module.

These tests verify:
1. TokenUsage dataclass operations (addition, totals)
2. Anthropic adapter correctly extracts usage from API responses
3. Braintrust exporter uses correct field names for cost calculation

The Braintrust field name tests are critical - incorrect names will cause
Braintrust to miscalculate costs.
"""

from dataclasses import dataclass

from chat.metrics import TokenUsage


class TestTokenUsageBasics:
    """Test basic TokenUsage operations."""

    def test_total_tokens_simple(self) -> None:
        usage = TokenUsage(input_tokens=100, output_tokens=50)
        assert usage.total_tokens == 150

    def test_total_tokens_with_cache(self) -> None:
        usage = TokenUsage(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=200,
            cache_read_input_tokens=75,
        )
        assert usage.total_tokens == 425

    def test_zero_creates_empty_usage(self) -> None:
        usage = TokenUsage.zero()
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.cache_creation_input_tokens == 0
        assert usage.cache_read_input_tokens == 0
        assert usage.total_tokens == 0

    def test_default_cache_tokens_are_zero(self) -> None:
        usage = TokenUsage(input_tokens=100, output_tokens=50)
        assert usage.cache_creation_input_tokens == 0
        assert usage.cache_read_input_tokens == 0


class TestTokenUsageAddition:
    """Test adding TokenUsage instances for multi-call aggregation."""

    def test_add_two_usages(self) -> None:
        usage1 = TokenUsage(input_tokens=100, output_tokens=50)
        usage2 = TokenUsage(input_tokens=80, output_tokens=30)
        total = usage1 + usage2
        assert total.input_tokens == 180
        assert total.output_tokens == 80

    def test_add_preserves_cache_tokens(self) -> None:
        usage1 = TokenUsage(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=200,
            cache_read_input_tokens=0,
        )
        usage2 = TokenUsage(
            input_tokens=80,
            output_tokens=30,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=200,
        )
        total = usage1 + usage2
        assert total.cache_creation_input_tokens == 200
        assert total.cache_read_input_tokens == 200

    def test_add_with_zero(self) -> None:
        usage = TokenUsage(input_tokens=100, output_tokens=50)
        total = usage + TokenUsage.zero()
        assert total.input_tokens == 100
        assert total.output_tokens == 50

    def test_sum_multiple_usages(self) -> None:
        """Verify we can sum a list of usages (common pattern for multi-turn)."""
        usages = [
            TokenUsage(input_tokens=100, output_tokens=50),
            TokenUsage(input_tokens=150, output_tokens=75),
            TokenUsage(input_tokens=200, output_tokens=100),
        ]
        total = TokenUsage.zero()
        for u in usages:
            total = total + u
        assert total.input_tokens == 450
        assert total.output_tokens == 225


class TestFromAnthropic:
    """Test extraction from Anthropic API response objects."""

    @dataclass
    class MockAnthropicUsage:
        """Mock Anthropic usage object for testing."""

        input_tokens: int
        output_tokens: int
        cache_creation_input_tokens: int | None = None
        cache_read_input_tokens: int | None = None

    def test_extracts_basic_tokens(self) -> None:
        mock_usage = self.MockAnthropicUsage(input_tokens=100, output_tokens=50)
        usage = TokenUsage.from_anthropic(mock_usage)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50

    def test_extracts_cache_tokens(self) -> None:
        mock_usage = self.MockAnthropicUsage(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=200,
            cache_read_input_tokens=75,
        )
        usage = TokenUsage.from_anthropic(mock_usage)
        assert usage.cache_creation_input_tokens == 200
        assert usage.cache_read_input_tokens == 75

    def test_handles_missing_cache_fields(self) -> None:
        """Anthropic responses may not include cache fields if not used."""

        @dataclass
        class MinimalUsage:
            input_tokens: int
            output_tokens: int

        mock_usage = MinimalUsage(input_tokens=100, output_tokens=50)
        usage = TokenUsage.from_anthropic(mock_usage)
        assert usage.cache_creation_input_tokens == 0
        assert usage.cache_read_input_tokens == 0

    def test_handles_none_cache_fields(self) -> None:
        """Cache fields may be present but None."""
        mock_usage = self.MockAnthropicUsage(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=None,
            cache_read_input_tokens=None,
        )
        usage = TokenUsage.from_anthropic(mock_usage)
        assert usage.cache_creation_input_tokens == 0
        assert usage.cache_read_input_tokens == 0


class TestToBraintrust:
    """Test Braintrust metrics export.

    CRITICAL: These tests verify that we use the exact field names Braintrust
    expects for cost calculation. Incorrect names = incorrect cost estimates.

    Expected mapping (from braintrust-sdk/_anthropic_utils.py):
        input_tokens → prompt_tokens
        output_tokens → completion_tokens
        cache_creation_input_tokens → prompt_cache_creation_tokens
        cache_read_input_tokens → prompt_cached_tokens
        total → tokens
    """

    def test_field_names_match_braintrust_sdk(self) -> None:
        """Verify we use the exact field names Braintrust expects."""
        usage = TokenUsage(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=200,
            cache_read_input_tokens=75,
        )
        metrics = usage.to_braintrust()

        # These exact names are required by Braintrust for cost calculation
        expected_keys = {
            "prompt_tokens",
            "completion_tokens",
            "prompt_cache_creation_tokens",
            "prompt_cached_tokens",
            "tokens",
        }
        assert set(metrics.keys()) == expected_keys

    def test_prompt_tokens_maps_from_input_tokens(self) -> None:
        """Braintrust expects 'prompt_tokens' not 'input_tokens'."""
        usage = TokenUsage(input_tokens=100, output_tokens=50)
        metrics = usage.to_braintrust()
        assert metrics["prompt_tokens"] == 100

    def test_completion_tokens_maps_from_output_tokens(self) -> None:
        """Braintrust expects 'completion_tokens' not 'output_tokens'."""
        usage = TokenUsage(input_tokens=100, output_tokens=50)
        metrics = usage.to_braintrust()
        assert metrics["completion_tokens"] == 50

    def test_cache_creation_uses_correct_braintrust_name(self) -> None:
        """Braintrust expects 'prompt_cache_creation_tokens' not 'cache_creation_tokens'."""
        usage = TokenUsage(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=200,
        )
        metrics = usage.to_braintrust()
        assert metrics["prompt_cache_creation_tokens"] == 200
        # Verify we don't accidentally use the wrong name
        assert "cache_creation_tokens" not in metrics
        assert "cache_creation_input_tokens" not in metrics

    def test_cache_read_uses_correct_braintrust_name(self) -> None:
        """Braintrust expects 'prompt_cached_tokens' not 'cache_read_tokens'."""
        usage = TokenUsage(
            input_tokens=100,
            output_tokens=50,
            cache_read_input_tokens=75,
        )
        metrics = usage.to_braintrust()
        assert metrics["prompt_cached_tokens"] == 75
        # Verify we don't accidentally use the wrong name
        assert "cache_read_tokens" not in metrics
        assert "cache_read_input_tokens" not in metrics

    def test_tokens_is_total(self) -> None:
        """Braintrust expects a 'tokens' field with the total."""
        usage = TokenUsage(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=200,
            cache_read_input_tokens=75,
        )
        metrics = usage.to_braintrust()
        assert metrics["tokens"] == 425  # 100 + 50 + 200 + 75

    def test_all_values_are_integers(self) -> None:
        """Braintrust expects integer metrics."""
        usage = TokenUsage(input_tokens=100, output_tokens=50)
        metrics = usage.to_braintrust()
        for key, value in metrics.items():
            assert isinstance(value, int), f"{key} should be int, got {type(value)}"


class TestBraintrustFieldNameRegistry:
    """Meta-tests to catch if Braintrust changes their expected field names.

    These tests document the expected mapping explicitly so if Braintrust
    updates their SDK, we can catch it here and update accordingly.
    """

    # The authoritative mapping from Braintrust SDK
    BRAINTRUST_EXPECTED_FIELDS = {
        "prompt_tokens",  # from input_tokens
        "completion_tokens",  # from output_tokens
        "prompt_cache_creation_tokens",  # from cache_creation_input_tokens
        "prompt_cached_tokens",  # from cache_read_input_tokens
        "tokens",  # total
    }

    def test_to_braintrust_produces_all_expected_fields(self) -> None:
        """Verify we produce exactly the fields Braintrust expects."""
        usage = TokenUsage(
            input_tokens=1,
            output_tokens=1,
            cache_creation_input_tokens=1,
            cache_read_input_tokens=1,
        )
        metrics = usage.to_braintrust()
        assert set(metrics.keys()) == self.BRAINTRUST_EXPECTED_FIELDS

    def test_no_extra_fields_in_braintrust_output(self) -> None:
        """Ensure we don't add unexpected fields that might confuse Braintrust."""
        usage = TokenUsage(input_tokens=100, output_tokens=50)
        metrics = usage.to_braintrust()
        extra_fields = set(metrics.keys()) - self.BRAINTRUST_EXPECTED_FIELDS
        assert not extra_fields, f"Unexpected fields in Braintrust output: {extra_fields}"
