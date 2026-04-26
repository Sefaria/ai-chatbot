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
