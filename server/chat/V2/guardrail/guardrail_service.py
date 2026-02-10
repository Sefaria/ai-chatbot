"""
Guardrail service — pre-agent LLM filter that classifies user messages.

Calls an LLM with a Braintrust-managed prompt to decide if a message is
within scope. Fails closed: any error → message blocked.
"""

import json
import logging
import os
from dataclasses import dataclass

import anthropic
from django.conf import settings

from ..prompts import get_prompt_service

logger = logging.getLogger("chat.guardrail")

GUARDRAIL_REJECTION_MESSAGE = (
    "I can only help with questions related to Jewish texts and Torah encyclopaedia available on Sefaria. "
    "Could you rephrase your question to be about a Jewish text or topic?"
)


@dataclass
class GuardrailResult:
    """Result of a guardrail check."""

    allowed: bool
    reason: str = ""


class GuardrailService:
    """Classifies user messages as allowed or blocked using an LLM filter.

    Uses a Braintrust-managed prompt (guardrail-checker) as the system prompt,
    sends the user message, and expects JSON {allowed, reason} back.
    Fails closed on any error.
    """

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(api_key=self.api_key) if self.api_key else None
        self.prompt_service = get_prompt_service()

    def check_message(self, user_message: str) -> GuardrailResult:
        """Check whether a user message is allowed.

        Returns GuardrailResult(allowed=True/False, reason=...).
        On any failure, returns allowed=False (fail closed).
        """
        if not self.client:
            logger.error("Guardrail: no Anthropic client configured")
            return GuardrailResult(allowed=False, reason="Guardrail service unavailable")

        try:
            system_prompt = self._load_prompt()
        except Exception as exc:
            logger.error(f"Guardrail: failed to load prompt: {exc}")
            return GuardrailResult(allowed=False, reason="Guardrail service unavailable")

        try:
            response = self.client.messages.create(
                model=settings.GUARDRAIL_MODEL,
                max_tokens=256,
                temperature=0.0,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            return self._parse_response(response)
        except Exception as exc:
            logger.error(f"Guardrail: LLM call failed: {exc}")
            return GuardrailResult(allowed=False, reason="Guardrail service unavailable")

    def _load_prompt(self) -> str:
        """Fetch the guardrail prompt from Braintrust via PromptService."""
        core_prompt = self.prompt_service.get_core_prompt(prompt_id=settings.GUARDRAIL_PROMPT_SLUG)
        return core_prompt.text

    def _parse_response(self, response) -> GuardrailResult:
        """Parse LLM response JSON. Fail closed on malformed output.

        Supports two response schemas:
        - Braintrust prompt format: {decision: "ALLOW"/"BLOCK", reason_codes, refusal_message}
        - Simple format: {allowed: bool, reason: str}

        Response may be wrapped in markdown code fences (```json ... ```).
        """
        try:
            text = response.content[0].text.strip()
            # Strip markdown code fences if present
            if text.startswith("```"):
                lines = text.split("\n")
                lines = [line for line in lines if not line.strip().startswith("```")]
                text = "\n".join(lines).strip()
            data = json.loads(text)

            # Braintrust prompt format: {decision: "ALLOW"/"BLOCK", ...}
            if "decision" in data:
                allowed = data["decision"].upper() == "ALLOW"
                reason = data.get("refusal_message") or ", ".join(data.get("reason_codes", []))
                return GuardrailResult(allowed=allowed, reason=reason or "")

            # Simple format: {allowed: bool, reason: str}
            allowed = data.get("allowed")
            if not isinstance(allowed, bool):
                logger.warning(f"Guardrail: 'allowed' not a bool: {text[:200]}")
                return GuardrailResult(allowed=False, reason="Malformed guardrail response")
            return GuardrailResult(
                allowed=allowed,
                reason=data.get("reason", ""),
            )
        except (json.JSONDecodeError, IndexError, KeyError) as exc:
            logger.warning(f"Guardrail: failed to parse response: {exc}")
            return GuardrailResult(allowed=False, reason="Malformed guardrail response")


_default_service: GuardrailService | None = None


def get_guardrail_service() -> GuardrailService:
    """Get or create the default guardrail service."""
    global _default_service
    if _default_service is None:
        _default_service = GuardrailService()
    return _default_service
