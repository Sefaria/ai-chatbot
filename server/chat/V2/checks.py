"""
Shared pre-flight checks for V2 chat endpoints.

Both views.py and anthropic_views.py call run_pre_flight_checks() before
running the agent. Centralises guardrail and multi-turn enforcement.
"""

import logging
from dataclasses import dataclass

from ..models import ChatSession
from .guardrail import get_guardrail_service

logger = logging.getLogger("chat.checks")

GUARDRAIL_REJECTION_MESSAGE = (
    "I can only help with questions related to Jewish texts and Torah encyclopaedia available on Sefaria. "
    "Could you rephrase your question to be about a Jewish text or topic?"
)

MULTI_TURN_REJECTION_MESSAGE = (
    "Multi-turn conversations are temporarily disabled. "
    "Please start a new conversation to ask another question."
)


@dataclass
class PreFlightResult:
    """Result of pre-flight checks."""

    passed: bool
    rejection_message: str = ""
    rejection_reason: str = ""
    rejection_type: str = ""  # "guardrail" or "multi_turn"


def run_pre_flight_checks(user_message_text: str, session: ChatSession) -> PreFlightResult:
    """Run all pre-flight checks on a user message.

    Returns PreFlightResult with passed=True if all checks pass,
    or passed=False with rejection details if any check fails.
    """
    # Check 1: Multi-turn limit — only one turn per session
    turn_count = session.turn_count if hasattr(session, "turn_count") else 0
    if (turn_count or 0) >= 1:
        logger.info(f"Multi-turn blocked: session has {turn_count} turns")
        return PreFlightResult(
            passed=False,
            rejection_message=MULTI_TURN_REJECTION_MESSAGE,
            rejection_reason="Multi-turn conversations are temporarily disabled",
            rejection_type="multi_turn",
        )

    # Check 2: Guardrail — is the message in scope?
    guardrail = get_guardrail_service()
    result = guardrail.check_message(user_message_text)
    if not result.allowed:
        logger.info(f"Guardrail blocked message: {result.reason}")
        return PreFlightResult(
            passed=False,
            rejection_message=GUARDRAIL_REJECTION_MESSAGE,
            rejection_reason=result.reason,
            rejection_type="guardrail",
        )

    return PreFlightResult(passed=True)
