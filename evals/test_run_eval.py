"""Tests for the evaluation script."""

import pytest
from unittest.mock import MagicMock, patch


class TestCreateScorer:
    """Tests for create_scorer function."""

    def test_creates_scorer_uses_slug_as_name_fallback(self):
        from evals.run_eval import create_scorer

        scorer = create_scorer("test-scorer")
        assert scorer.__name__ == "test-scorer"

    def test_creates_scorer_uses_display_name_when_provided(self):
        from evals.run_eval import create_scorer

        scorer = create_scorer("test-scorer", name="Test Scorer")
        assert scorer.__name__ == "Test Scorer"

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
        from evals.run_eval import SCORER_MAX_ATTEMPTS, create_scorer

        scorer = create_scorer("my-scorer")
        with (
            patch("evals.run_eval.invoke") as mock_invoke,
            patch("evals.run_eval.time.sleep"),
        ):
            mock_invoke.side_effect = Exception("API error")
            with pytest.raises(Exception, match="API error"):
                scorer("test output")
            assert mock_invoke.call_count == SCORER_MAX_ATTEMPTS

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
            # Malformed response is deterministic — must not retry.
            assert mock_invoke.call_count == 1

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

    def test_scorer_unwraps_content_for_dict_outputs(self):
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
            # Cost/latency are span metrics now, not scorer metadata.
            assert call_kwargs["input"]["metadata"] == {"trial": 1}


class TestIsBraintrustAuthError:
    """Tests for the narrowed _is_braintrust_auth_error heuristic."""

    def test_detects_401_status_code(self):
        from evals.run_eval import _is_braintrust_auth_error

        class FakeResponse:
            status_code = 401

        err = Exception("something went wrong")
        err.response = FakeResponse()
        assert _is_braintrust_auth_error(err) is True

    def test_detects_403_status_code(self):
        from evals.run_eval import _is_braintrust_auth_error

        class FakeResponse:
            status_code = 403

        err = Exception("something went wrong")
        err.response = FakeResponse()
        assert _is_braintrust_auth_error(err) is True

    @pytest.mark.parametrize(
        "message",
        [
            "401 Unauthorized",
            "HTTP 403 Forbidden",
            "JWT signature expired",
            "Access token is invalid",
            "token expired",
            "Invalid token provided",
        ],
    )
    def test_detects_auth_keywords(self, message):
        from evals.run_eval import _is_braintrust_auth_error

        assert _is_braintrust_auth_error(Exception(message)) is True

    @pytest.mark.parametrize(
        "message",
        [
            "token limit exceeded",
            "tokenizer failed",
            "max tokens reached",
            "connection reset by peer",
            "500 Internal Server Error",
        ],
    )
    def test_ignores_non_auth_errors(self, message):
        from evals.run_eval import _is_braintrust_auth_error

        assert _is_braintrust_auth_error(Exception(message)) is False


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


class TestGetMean:
    """Tests for _get_mean — must accept every shape Braintrust uses.

    Missing the ScoreSummary `.score` attribute previously caused
    analyze_threshold to treat every current score as None, print an empty
    table, and announce READY TO MERGE regardless of actual regression."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            (None, None),
            (0.5, 0.5),
            (1, 1.0),  # int coerced to float
            ({}, None),  # empty dict — no recognized key
            (object(), None),  # opaque object — no recognized attr or float()
        ],
    )
    def test_scalar_and_edge_inputs(self, value, expected):
        from evals.run_eval import _get_mean

        assert _get_mean(value) == expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            # Older SDK / REST shape — `mean` wins over `score`.
            ({"mean": 0.8, "score": 0.4}, 0.8),
            # experiment-comparison2 returns ScoreSummary dicts keyed by `score`.
            ({"score": 0.42, "improvements": 3}, 0.42),
            # MetricSummary dicts use `metric`.
            ({"metric": 4.2, "unit": "s"}, 4.2),
        ],
    )
    def test_dict_shapes(self, value, expected):
        from evals.run_eval import _get_mean

        assert _get_mean(value) == expected

    def test_score_summary_object(self):
        from evals.run_eval import _get_mean

        # Regression guard: ScoreSummary exposes `.score`, not `.mean`. The
        # object branch must find it instead of falling through to float().
        score_summary = MagicMock(spec=["score", "name", "improvements"])
        score_summary.score = 0.73
        assert _get_mean(score_summary) == 0.73

    def test_metric_summary_object(self):
        from evals.run_eval import _get_mean

        metric_summary = MagicMock(spec=["metric", "name", "unit"])
        metric_summary.metric = 4.2
        assert _get_mean(metric_summary) == 4.2

    def test_object_with_mean_attribute(self):
        from evals.run_eval import _get_mean

        obj = MagicMock(spec=["mean"])
        obj.mean = 0.9
        assert _get_mean(obj) == 0.9


class TestFetchExperimentScores:
    """Tests for fetch_experiment_scores — the wrong endpoint silently 403'd
    and skipped baseline comparison. Pin the right one with a unit test."""

    def test_calls_experiment_comparison2_endpoint(self):
        from evals.run_eval import fetch_experiment_scores

        fake_conn = MagicMock()
        fake_conn.get_json.return_value = {"scores": {"acc": {"score": 0.9}}}
        with patch("evals.run_eval.braintrust.api_conn", return_value=fake_conn):
            result = fetch_experiment_scores("exp-123")

        fake_conn.get_json.assert_called_once_with(
            "experiment-comparison2", args={"experiment_id": "exp-123"}
        )
        assert result == {"acc": {"score": 0.9}}

    def test_returns_empty_when_response_has_no_scores(self):
        from evals.run_eval import fetch_experiment_scores

        fake_conn = MagicMock()
        fake_conn.get_json.return_value = {"metrics": {}}
        with patch("evals.run_eval.braintrust.api_conn", return_value=fake_conn):
            assert fetch_experiment_scores("exp-123") == {}

    def test_returns_empty_on_exception(self, capsys):
        from evals.run_eval import fetch_experiment_scores

        fake_conn = MagicMock()
        fake_conn.get_json.side_effect = RuntimeError("boom")
        with patch("evals.run_eval.braintrust.api_conn", return_value=fake_conn):
            assert fetch_experiment_scores("exp-123") == {}

        # User-visible warning so a silent skip can't masquerade as success.
        assert "Could not fetch experiment scores" in capsys.readouterr().err


class TestPinExperiment:
    """Regression guard: pin_experiment must PATCH against the SDK conn's
    configured base_url, not a hardcoded api.braintrust.dev — otherwise
    self-hosted Braintrust deployments break, and we'd quietly skip the auth
    header that the conn's session already sets."""

    def test_patches_via_conn_session_with_conn_base_url(self):
        from evals import run_eval

        fake_resp_get = MagicMock(ok=True)
        fake_resp_get.json.return_value = {
            "objects": [{"id": "proj-123", "name": run_eval.PROJECT}]
        }
        fake_resp_patch = MagicMock(ok=True)

        fake_conn = MagicMock()
        fake_conn.base_url = "https://braintrust.example.com"
        fake_conn.get.return_value = fake_resp_get
        fake_conn.session.patch.return_value = fake_resp_patch

        with patch("evals.run_eval.braintrust.api_conn", return_value=fake_conn):
            assert run_eval.pin_experiment("exp-9", "My Run") is True

        # URL must come from conn.base_url + the routes constant, not a hardcoded host.
        called_url = fake_conn.session.patch.call_args.args[0]
        assert called_url == "https://braintrust.example.com/v1/project/proj-123"
        assert fake_conn.session.patch.call_args.kwargs["json"] == {
            "settings": {"baseline_experiment_id": "exp-9"}
        }


class TestTaskMetricLogging:
    """Tests that the eval task logs cost/latency as Braintrust span metrics."""

    @pytest.mark.asyncio
    async def test_task_logs_cost_and_latency_metrics(self):
        from evals import run_eval

        chat_response = {
            "content": "hello",
            "totalCostUsd": 0.0123,
            "latencyMs": 4200,
        }

        class FakeClient:
            base_url = "http://test"

            async def chat(self, prompt):
                return chat_response

            async def close(self):
                return None

        recorded = {}

        class FakeSpan:
            def log(self, **kwargs):
                recorded.update(kwargs)

        async def fake_eval_async(*args, **kwargs):
            await kwargs["task"]({"prompt": "hi"})

        with (
            patch("evals.run_eval.current_span", return_value=FakeSpan()),
            patch("evals.run_eval.EvalAsync", side_effect=fake_eval_async),
            patch("evals.run_eval.init_dataset"),
        ):
            await run_eval.run_evaluation(
                client=FakeClient(),
                dataset_name="ds",
                experiment_name="exp",
                scorers=[],
                max_concurrency=1,
            )

        assert recorded["metrics"] == {"cost_usd": 0.0123, "latency_seconds": 4.2}

    @pytest.mark.asyncio
    async def test_task_skips_metrics_when_stats_missing(self):
        from evals import run_eval

        class FakeClient:
            base_url = "http://test"

            async def chat(self, prompt):
                return {"content": "hello", "totalCostUsd": None, "latencyMs": None}

            async def close(self):
                return None

        log_calls = []

        class FakeSpan:
            def log(self, **kwargs):
                log_calls.append(kwargs)

        async def fake_eval_async(*args, **kwargs):
            await kwargs["task"]({"prompt": "hi"})

        with (
            patch("evals.run_eval.current_span", return_value=FakeSpan()),
            patch("evals.run_eval.EvalAsync", side_effect=fake_eval_async),
            patch("evals.run_eval.init_dataset"),
        ):
            await run_eval.run_evaluation(
                client=FakeClient(),
                dataset_name="ds",
                experiment_name="exp",
                scorers=[],
                max_concurrency=1,
            )

        # Nothing to log when the server didn't emit stats.
        assert log_calls == []
