"""Tests for the evaluation script."""

import pytest
from unittest.mock import patch


class TestCreateScorer:
    """Tests for create_scorer function."""

    def test_creates_scorer_with_correct_name(self):
        from evals.run_eval import create_scorer

        scorer = create_scorer("test-scorer")
        assert scorer.__name__ == "test_scorer"

    def test_scorer_calls_invoke_with_output(self):
        from evals.run_eval import create_scorer

        scorer = create_scorer("my-scorer")
        with patch("evals.run_eval.invoke") as mock_invoke:
            mock_invoke.return_value = {"score": 0.8}
            result = scorer("test output")
            assert result == 0.8
            mock_invoke.assert_called_once()
            call_args = mock_invoke.call_args
            assert call_args.kwargs["input"]["output"] == "test output"

    def test_scorer_includes_expected_when_provided(self):
        from evals.run_eval import create_scorer

        scorer = create_scorer("my-scorer")
        with patch("evals.run_eval.invoke") as mock_invoke:
            mock_invoke.return_value = {"score": 1.0}
            scorer("output", expected="expected value")
            call_args = mock_invoke.call_args
            assert call_args.kwargs["input"]["expected"] == "expected value"

    def test_scorer_raises_after_exhausted_retries(self):
        from evals.run_eval import create_scorer

        scorer = create_scorer("my-scorer")
        with (
            patch("evals.run_eval.invoke") as mock_invoke,
            patch("evals.run_eval.time.sleep"),
        ):
            mock_invoke.side_effect = Exception("API error")
            with pytest.raises(Exception, match="API error"):
                scorer("test output")
            assert mock_invoke.call_count == 3

    def test_scorer_raises_when_dict_missing_score_key(self):
        from evals.run_eval import create_scorer

        scorer = create_scorer("my-scorer")
        with (
            patch("evals.run_eval.invoke") as mock_invoke,
            patch("evals.run_eval.time.sleep"),
        ):
            mock_invoke.return_value = {"pass": True, "reason": "looks good"}
            with pytest.raises(ValueError, match="without 'score'"):
                scorer("test output")

    def test_scorer_succeeds_after_transient_error(self):
        from evals.run_eval import create_scorer

        scorer = create_scorer("my-scorer")
        with (
            patch("evals.run_eval.invoke") as mock_invoke,
            patch("evals.run_eval.time.sleep"),
        ):
            mock_invoke.side_effect = [Exception("boom"), {"score": 0.9}]
            assert scorer("out") == 0.9
            assert mock_invoke.call_count == 2
            # First attempt does not force_login (no prior auth error)
            assert mock_invoke.call_args_list[0].kwargs["force_login"] is False

    def test_scorer_forces_login_after_auth_error(self):
        from evals.run_eval import create_scorer

        class FakeResponse:
            status_code = 401

        err = Exception("Unauthorized")
        err.response = FakeResponse()

        scorer = create_scorer("my-scorer")
        with (
            patch("evals.run_eval.invoke") as mock_invoke,
            patch("evals.run_eval.time.sleep"),
        ):
            mock_invoke.side_effect = [err, {"score": 0.5}]
            assert scorer("out") == 0.5
            # Retry after a 401 must force a fresh Braintrust login / JWT
            assert mock_invoke.call_args_list[1].kwargs["force_login"] is True

    def test_scorer_returns_raw_value_when_not_dict(self):
        from evals.run_eval import create_scorer

        scorer = create_scorer("my-scorer")
        with patch("evals.run_eval.invoke") as mock_invoke:
            mock_invoke.return_value = 0.75
            result = scorer("test output")
            assert result == 0.75

    def test_scorer_unwraps_content_and_folds_stats_into_metadata(self):
        from evals.run_eval import create_scorer

        scorer = create_scorer("my-scorer")
        task_output = {
            "content": "hello world",
            "totalCostUsd": 0.0123,
            "latencyMs": 4200,
        }
        with patch("evals.run_eval.invoke") as mock_invoke:
            mock_invoke.return_value = {"score": 0.9}
            scorer(task_output, metadata={"trial": 1})
            call_kwargs = mock_invoke.call_args.kwargs
            # Pre-existing LLM scorers receive a plain string, not a dict.
            assert call_kwargs["input"]["output"] == "hello world"
            # Cost/latency are surfaced via metadata so code scorers can read them.
            assert call_kwargs["input"]["metadata"]["totalCostUsd"] == 0.0123
            assert call_kwargs["input"]["metadata"]["latencyMs"] == 4200
            assert call_kwargs["input"]["metadata"]["trial"] == 1

    def test_scorer_preserves_caller_metadata_overrides(self):
        from evals.run_eval import create_scorer

        scorer = create_scorer("my-scorer")
        task_output = {"content": "x", "totalCostUsd": 0.01, "latencyMs": 1}
        with patch("evals.run_eval.invoke") as mock_invoke:
            mock_invoke.return_value = {"score": 1.0}
            # Caller-supplied metadata wins over task output stats.
            scorer(task_output, metadata={"totalCostUsd": 0.99})
            md = mock_invoke.call_args.kwargs["input"]["metadata"]
            assert md["totalCostUsd"] == 0.99
            assert md["latencyMs"] == 1


class TestChatbotClient:
    """Tests for ChatbotClient."""

    def test_requires_user_token(self):
        from evals.run_eval import ChatbotClient

        with patch("evals.run_eval.USER_TOKEN", None):
            with pytest.raises(ValueError, match="CHATBOT_USER_TOKEN"):
                ChatbotClient(base_url="http://localhost:8001")

    def test_strips_trailing_slash_from_base_url(self):
        from evals.run_eval import ChatbotClient

        with patch("evals.run_eval.USER_TOKEN", "test-token"):
            client = ChatbotClient(base_url="http://localhost:8001/")
            assert client.base_url == "http://localhost:8001"


def _fake_sse_stream(final_payload: dict):
    """Build a fake httpx streaming-response context that yields one SSE frame."""
    import json as _json

    class _FakeResponse:
        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            yield "event: message"
            yield f"data: {_json.dumps(final_payload)}"
            yield ""

    class _FakeStream:
        async def __aenter__(self):
            return _FakeResponse()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    return _FakeStream()


class TestChatbotClientChat:
    """Tests for ChatbotClient.chat() stats plumbing."""

    @pytest.mark.asyncio
    async def test_chat_returns_content_and_stats(self):
        from evals.run_eval import ChatbotClient

        with patch("evals.run_eval.USER_TOKEN", "test-token"):
            client = ChatbotClient(base_url="http://localhost:8001")

        final = {
            "markdown": "hello world",
            "stats": {"totalCostUsd": 0.0123, "latencyMs": 4200},
        }
        client.client.stream = lambda *a, **kw: _fake_sse_stream(final)

        result = await client.chat("hi")
        assert result == {
            "content": "hello world",
            "totalCostUsd": 0.0123,
            "latencyMs": 4200,
        }

    @pytest.mark.asyncio
    async def test_chat_missing_stats_degrades_to_none(self):
        from evals.run_eval import ChatbotClient

        with patch("evals.run_eval.USER_TOKEN", "test-token"):
            client = ChatbotClient(base_url="http://localhost:8001")

        # Server didn't emit a stats dict — scorers should see None, not raise.
        final = {"markdown": "hello"}
        client.client.stream = lambda *a, **kw: _fake_sse_stream(final)

        result = await client.chat("hi")
        assert result == {
            "content": "hello",
            "totalCostUsd": None,
            "latencyMs": None,
        }


class TestCostScorer:
    """Tests for the cost_usd code scorer handler."""

    def test_reads_cost_from_output(self):
        from evals.scorers.code_scorers.cost_usd import handler

        result = handler(
            input=None,
            output={"content": "x", "totalCostUsd": 0.015, "latencyMs": 1000},
            expected=None,
            metadata={},
        )
        assert result["score"] == 0.015
        assert result["metadata"]["cost_usd"] == 0.015

    def test_falls_back_to_span_metadata(self):
        from evals.scorers.code_scorers.cost_usd import handler

        # Pushed-scorer path: output is missing, metadata carries span data.
        result = handler(
            input=None,
            output=None,
            expected=None,
            metadata={"totalCostUsd": 0.04},
        )
        assert result["score"] == 0.04

    def test_missing_value_returns_none_score(self):
        from evals.scorers.code_scorers.cost_usd import handler

        result = handler(
            input=None, output={"content": "x"}, expected=None, metadata={}
        )
        assert result["score"] is None
        assert "reason" in result["metadata"]


class TestLatencyScorer:
    """Tests for the latency_ms code scorer handler."""

    def test_reads_latency_from_output(self):
        from evals.scorers.code_scorers.latency_ms import handler

        result = handler(
            input=None,
            output={"content": "x", "totalCostUsd": 0.0, "latencyMs": 5500},
            expected=None,
            metadata={},
        )
        assert result["score"] == 5500
        assert result["metadata"]["latency_ms"] == 5500

    def test_falls_back_to_span_metadata(self):
        from evals.scorers.code_scorers.latency_ms import handler

        result = handler(
            input=None,
            output=None,
            expected=None,
            metadata={"latencyMs": 2345},
        )
        assert result["score"] == 2345

    def test_missing_value_returns_none_score(self):
        from evals.scorers.code_scorers.latency_ms import handler

        result = handler(
            input=None, output={"content": "x"}, expected=None, metadata={}
        )
        assert result["score"] is None
        assert "reason" in result["metadata"]
