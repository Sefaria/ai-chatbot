"""
Shared pre-flight checks for V2 chat endpoints.

Both views.py and anthropic_views.py call run_pre_flight_checks() before
running the agent. Centralises guardrail enforcement.
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


@dataclass
class PreFlightResult:
    """Result of pre-flight checks."""

    passed: bool
    rejection_message: str = ""
    rejection_reason: str = ""
    rejection_type: str = ""  # "guardrail"


def run_pre_flight_checks(user_message_text: str, session: ChatSession) -> PreFlightResult:
    """Run all pre-flight checks on a user message.

    Returns PreFlightResult with passed=True if all checks pass,
    or passed=False with rejection details if any check fails.
    """
    # Guardrail — is the message in scope?
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
