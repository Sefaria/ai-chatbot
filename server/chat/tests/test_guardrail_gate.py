"""Tests for DefaultGuardrailGate — rejection message selection logic."""

import asyncio
from unittest.mock import MagicMock, patch

from chat.V2.agent.contracts import AgentResponse, MessageContext
from chat.V2.agent.guardrail_gate import DefaultGuardrailGate
from chat.V2.guardrail.guardrail_service import GuardrailResult
from chat.V2.prompts.prompt_fragments import (
    GUARDRAIL_MALFORMED_REASON,
    GUARDRAIL_REJECTION_FALLBACK,
    GUARDRAIL_UNAVAILABLE_REASON,
)


def _make_context():
    return MessageContext()


def _make_bt_span():
    span = MagicMock()
    span.start_span.return_value = MagicMock()
    span.id = "test-span-id"
    return span


def _run(coro):
    return asyncio.run(coro)


class TestGuardrailGateRejection:
    """Test that run_guardrail maps guardrail results to correct user-facing messages."""

    def _run_gate(self, guardrail_result: GuardrailResult) -> AgentResponse | None:
        gate = DefaultGuardrailGate()
        bt_span = _make_bt_span()
        with patch("chat.V2.agent.guardrail_gate.get_guardrail_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.check_message.return_value = guardrail_result
            mock_get_service.return_value = mock_service
            return _run(
                gate.run_guardrail(
                    bt_span=bt_span,
                    user_message="test message",
                    context=_make_context(),
                    start_time=0.0,
                )
            )

    def test_allowed_returns_none(self):
        result = self._run_gate(GuardrailResult(allowed=True, reason="On-topic"))
        assert result is None

    def test_block_with_reason_passes_through(self):
        reason = (
            "I appreciate your question, but I can only help with topics related to Jewish texts."
        )
        result = self._run_gate(GuardrailResult(allowed=False, reason=reason))
        assert result is not None
        assert result.content == reason

    def test_unavailable_reason_uses_fallback(self):
        result = self._run_gate(GuardrailResult(allowed=False, reason=GUARDRAIL_UNAVAILABLE_REASON))
        assert result is not None
        assert result.content == GUARDRAIL_REJECTION_FALLBACK

    def test_malformed_reason_uses_fallback(self):
        result = self._run_gate(GuardrailResult(allowed=False, reason=GUARDRAIL_MALFORMED_REASON))
        assert result is not None
        assert result.content == GUARDRAIL_REJECTION_FALLBACK

    def test_empty_reason_uses_fallback(self):
        result = self._run_gate(GuardrailResult(allowed=False, reason=""))
        assert result is not None
        assert result.content == GUARDRAIL_REJECTION_FALLBACK
