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

    def test_scorer_returns_zero_on_error(self):
        from evals.run_eval import create_scorer

        scorer = create_scorer("my-scorer")
        with patch("evals.run_eval.invoke") as mock_invoke:
            mock_invoke.side_effect = Exception("API error")
            result = scorer("test output")
            assert result == 0.0

    def test_scorer_returns_zero_when_dict_missing_score_key(self):
        from evals.run_eval import create_scorer

        scorer = create_scorer("my-scorer")
        with patch("evals.run_eval.invoke") as mock_invoke:
            mock_invoke.return_value = {"pass": True, "reason": "looks good"}
            result = scorer("test output")
            assert result == 0.0

    def test_scorer_returns_raw_value_when_not_dict(self):
        from evals.run_eval import create_scorer

        scorer = create_scorer("my-scorer")
        with patch("evals.run_eval.invoke") as mock_invoke:
            mock_invoke.return_value = 0.75
            result = scorer("test output")
            assert result == 0.75


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
