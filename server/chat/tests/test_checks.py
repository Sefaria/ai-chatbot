"""Tests for shared pre-flight checks."""

from unittest.mock import MagicMock, patch

import pytest

from chat.V2.checks import run_pre_flight_checks
from chat.V2.guardrail.guardrail_service import GuardrailResult


@pytest.mark.django_db
class TestPreFlightChecks:
    """Test run_pre_flight_checks with mocked guardrail."""

    def _make_session(self, turn_count=0):
        session = MagicMock()
        session.turn_count = turn_count
        return session

    @patch("chat.V2.checks.get_guardrail_service")
    def test_passes_when_allowed(self, mock_get_guardrail):
        mock_service = MagicMock()
        mock_service.check_message.return_value = GuardrailResult(allowed=True)
        mock_get_guardrail.return_value = mock_service

        result = run_pre_flight_checks("What is Shabbat?", self._make_session())
        assert result.passed is True

    @patch("chat.V2.checks.get_guardrail_service")
    def test_blocked_by_guardrail(self, mock_get_guardrail):
        mock_service = MagicMock()
        mock_service.check_message.return_value = GuardrailResult(allowed=False, reason="Off-topic")
        mock_get_guardrail.return_value = mock_service

        result = run_pre_flight_checks("What is the capital of France?", self._make_session())
        assert result.passed is False
        assert result.rejection_type == "guardrail"
        assert result.rejection_reason == "Off-topic"
        assert result.rejection_message  # non-empty

    def test_multi_turn_blocked(self):
        result = run_pre_flight_checks("Follow-up question", self._make_session(turn_count=1))
        assert result.passed is False
        assert result.rejection_type == "multi_turn"

    def test_multi_turn_allowed_on_first_turn(self):
        result = run_pre_flight_checks("First question", self._make_session(turn_count=0))
        assert result.passed is True

    def test_multi_turn_checked_before_guardrail(self):
        """Multi-turn check runs first, so guardrail is never called for multi-turn."""
        result = run_pre_flight_checks("Anything", self._make_session(turn_count=2))
        assert result.passed is False
        assert result.rejection_type == "multi_turn"
