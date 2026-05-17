"""Tests for the pricing utility."""

import contextvars
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from chat.V2.pricing import (
    CostAccumulator,
    bind_cost_accumulator,
    compute_cost,
    get_cost_accumulator,
    init_cost_accumulator,
    reset_cost_accumulator,
    tracked_messages_create,
)

# Deterministic fixture prices — chosen so each token category contributes a
# distinct amount. Tests assert the math against these constants instead of
# real LiteLLM prices, which refresh weekly via the update-pricing workflow.
TEST_MODEL = "test-model"
INPUT_PRICE = 1e-06
OUTPUT_PRICE = 1e-05
CACHE_WRITE_PRICE = 2e-06
CACHE_READ_PRICE = 1e-07


@pytest.fixture(autouse=True)
def fixed_pricing(monkeypatch):
    """Install a deterministic pricing table so tests don't break when
    model_pricing.json is regenerated from upstream LiteLLM data."""
    monkeypatch.setattr(
        "chat.V2.pricing._MODEL_PRICING",
        {
            TEST_MODEL: {
                "input_cost_per_token": INPUT_PRICE,
                "output_cost_per_token": OUTPUT_PRICE,
                "cache_creation_input_token_cost": CACHE_WRITE_PRICE,
                "cache_read_input_token_cost": CACHE_READ_PRICE,
            },
        },
    )


class TestComputeCost:
    def test_basic_cost(self):
        cost = compute_cost(TEST_MODEL, input_tokens=1000, output_tokens=100)
        expected = (1000 * INPUT_PRICE) + (100 * OUTPUT_PRICE)
        assert cost == pytest.approx(expected)

    def test_unknown_model_returns_none(self):
        cost = compute_cost("unknown-model", input_tokens=100, output_tokens=50)
        assert cost is None

    def test_zero_tokens(self):
        cost = compute_cost(TEST_MODEL, input_tokens=0, output_tokens=0)
        assert cost == 0.0

    def test_cache_tokens_included(self):
        cost = compute_cost(
            TEST_MODEL,
            input_tokens=1000,
            output_tokens=100,
            cache_creation_tokens=500,
            cache_read_tokens=2000,
        )
        expected = (
            (1000 * INPUT_PRICE)
            + (100 * OUTPUT_PRICE)
            + (500 * CACHE_WRITE_PRICE)
            + (2000 * CACHE_READ_PRICE)
        )
        assert cost == pytest.approx(expected)

    def test_cache_tokens_default_to_zero(self):
        without = compute_cost(TEST_MODEL, input_tokens=1000, output_tokens=100)
        with_zero_cache = compute_cost(
            TEST_MODEL,
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
        acc.add(TEST_MODEL, input_tokens=1000, output_tokens=100)
        expected = (1000 * INPUT_PRICE) + (100 * OUTPUT_PRICE)
        assert acc.total == pytest.approx(expected)

    def test_add_unknown_model_ignored(self):
        acc = CostAccumulator()
        acc.add("unknown-model", input_tokens=1000, output_tokens=100)
        assert acc.total == 0.0

    def test_add_with_cache_tokens(self):
        acc = CostAccumulator()
        acc.add(
            TEST_MODEL,
            input_tokens=1000,
            output_tokens=100,
            cache_creation_tokens=500,
            cache_read_tokens=2000,
        )
        expected = (
            (1000 * INPUT_PRICE)
            + (100 * OUTPUT_PRICE)
            + (500 * CACHE_WRITE_PRICE)
            + (2000 * CACHE_READ_PRICE)
        )
        assert acc.total == pytest.approx(expected)

    def test_accumulates_multiple_calls(self):
        acc = CostAccumulator()
        acc.add(TEST_MODEL, input_tokens=1000, output_tokens=100)
        first = acc.total
        acc.add(TEST_MODEL, input_tokens=1000, output_tokens=100)
        assert acc.total == pytest.approx(first * 2)

    def test_context_var_lifecycle(self):
        # Reset first — other tests (or prior requests on a reused WSGI thread)
        # may have left a stale accumulator on the ContextVar.
        reset_cost_accumulator()
        assert get_cost_accumulator() is None
        acc = init_cost_accumulator()
        assert get_cost_accumulator() is acc
        acc.add(TEST_MODEL, input_tokens=500, output_tokens=50)
        assert get_cost_accumulator().total == acc.total
        reset_cost_accumulator()
        assert get_cost_accumulator() is None

    def test_bind_restores_accumulator_in_fresh_context(self):
        """Load-test path submits run_agent without contextvars.copy_context().
        Without explicit rebinding, the thread's context has no accumulator
        and guardrail/router's get_cost_accumulator() would return None.
        bind_cost_accumulator() restores it inside the thread.
        """
        import threading

        reset_cost_accumulator()
        outer_acc = init_cost_accumulator()
        seen_inside = {}

        def worker():
            # Fresh context (as if started with no copy_context): no accumulator
            seen_inside["before_bind"] = get_cost_accumulator()
            bind_cost_accumulator(outer_acc)
            seen_inside["after_bind"] = get_cost_accumulator()
            get_cost_accumulator().add(TEST_MODEL, input_tokens=1000, output_tokens=100)

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        assert seen_inside["before_bind"] is None
        assert seen_inside["after_bind"] is outer_acc
        # Mutation is visible back on the outer accumulator (shared reference)
        assert outer_acc.total > 0
        reset_cost_accumulator()

    def test_copied_context_sees_same_accumulator(self):
        """The orchestrator dispatches the agent via contextvars.copy_context().
        Mutations on the agent thread must be visible back in the caller —
        this is what makes guardrail/router cost tracking work through
        asyncio.to_thread boundaries. Regression guard: if the agent is ever
        dispatched before init_cost_accumulator(), the copied context won't
        have the accumulator and this test will fail.
        """
        reset_cost_accumulator()
        acc = init_cost_accumulator()
        ctx = contextvars.copy_context()

        def add_in_copied_context():
            get_cost_accumulator().add(TEST_MODEL, input_tokens=1000, output_tokens=100)

        ctx.run(add_in_copied_context)

        # The accumulator is shared by reference, so the outer view sees it.
        assert acc.total > 0
        reset_cost_accumulator()


class TestAddFromResponse:
    @staticmethod
    def _mock_response(
        input_tokens: int = 10,
        output_tokens: int = 5,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=None,
    ):
        """Build a duck-typed Anthropic Messages response (only .usage matters)."""
        usage = SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
        )
        return SimpleNamespace(usage=usage)

    def test_basic(self):
        acc = CostAccumulator()
        acc.add_from_response(
            TEST_MODEL,
            self._mock_response(input_tokens=1000, output_tokens=100),
        )
        expected = (1000 * INPUT_PRICE) + (100 * OUTPUT_PRICE)
        assert acc.total == pytest.approx(expected)

    def test_cache_fields_none(self):
        """SDK sets cache_* to None for non-caching models. Must not crash."""
        acc = CostAccumulator()
        acc.add_from_response(
            TEST_MODEL,
            self._mock_response(
                input_tokens=1000,
                output_tokens=100,
                cache_creation_input_tokens=None,
                cache_read_input_tokens=None,
            ),
        )
        expected = (1000 * INPUT_PRICE) + (100 * OUTPUT_PRICE)
        assert acc.total == pytest.approx(expected)

    def test_cache_fields_missing(self):
        """Older SDK/response shapes may lack the attrs entirely."""
        acc = CostAccumulator()
        usage = SimpleNamespace(input_tokens=1000, output_tokens=100)
        response = SimpleNamespace(usage=usage)
        acc.add_from_response(TEST_MODEL, response)
        expected = (1000 * INPUT_PRICE) + (100 * OUTPUT_PRICE)
        assert acc.total == pytest.approx(expected)

    def test_cache_fields_populated(self):
        acc = CostAccumulator()
        acc.add_from_response(
            TEST_MODEL,
            self._mock_response(
                input_tokens=1000,
                output_tokens=100,
                cache_creation_input_tokens=500,
                cache_read_input_tokens=2000,
            ),
        )
        expected = (
            (1000 * INPUT_PRICE)
            + (100 * OUTPUT_PRICE)
            + (500 * CACHE_WRITE_PRICE)
            + (2000 * CACHE_READ_PRICE)
        )
        assert acc.total == pytest.approx(expected)


class TestTrackedMessagesCreate:
    @staticmethod
    def _make_client(input_tokens=1000, output_tokens=100):
        usage = SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=None,
            cache_read_input_tokens=None,
        )
        client = MagicMock()
        client.messages.create.return_value = SimpleNamespace(usage=usage)
        return client

    def test_forwards_kwargs_and_returns_response(self):
        reset_cost_accumulator()
        client = self._make_client()
        response = tracked_messages_create(client, model=TEST_MODEL, max_tokens=10)
        client.messages.create.assert_called_once_with(model=TEST_MODEL, max_tokens=10)
        assert response is client.messages.create.return_value

    def test_appends_cost_to_bound_accumulator(self):
        reset_cost_accumulator()
        acc = init_cost_accumulator()
        client = self._make_client(input_tokens=1000, output_tokens=100)
        tracked_messages_create(client, model=TEST_MODEL)
        expected = (1000 * INPUT_PRICE) + (100 * OUTPUT_PRICE)
        assert acc.total == pytest.approx(expected)
        reset_cost_accumulator()

    def test_no_op_when_no_accumulator_bound(self):
        reset_cost_accumulator()
        client = self._make_client()
        tracked_messages_create(client, model=TEST_MODEL)
        assert get_cost_accumulator() is None

    def test_no_op_when_model_kwarg_missing(self):
        reset_cost_accumulator()
        acc = init_cost_accumulator()
        client = self._make_client()
        tracked_messages_create(client, max_tokens=10)
        assert acc.total == 0.0
        reset_cost_accumulator()
