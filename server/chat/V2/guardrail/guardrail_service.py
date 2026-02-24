"""
Guardrail service — pre-agent LLM filter that classifies user messages.

Calls an LLM with a Braintrust-managed prompt to decide if a message is
within scope. Fails closed: any error → message blocked.
"""

import json
import logging
from dataclasses import dataclass

from django.conf import settings

from ..prompts import get_prompt_service
from ..utils import get_anthropic_client, make_singleton

logger = logging.getLogger("chat.guardrail")

# JSON schema for structured output — guarantees valid JSON with constrained
# decision values directly from the API, eliminating parsing edge cases.
GUARDRAIL_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["ALLOW", "BLOCK"]},
        "reason": {"type": "string"},
        "message": {"type": "string"},
    },
    "required": ["decision", "reason", "message"],
    "additionalProperties": False,
}


@dataclass
class GuardrailResult:
    """Result of a guardrail check."""

    allowed: bool
    reason: str = ""
    message: str = ""


class GuardrailService:
    """Classifies user messages as allowed or blocked using an LLM filter.

    Uses a Braintrust-managed prompt (guardrail-checker) as the system prompt,
    sends the user message, and expects JSON {decision, reason, message} back.
    Uses structured outputs (output_config) to guarantee valid JSON from the API.
    When blocked, `message` is sent directly to the user.
    Fails closed on any error.
    """

    def __init__(self, api_key: str | None = None):
        self.client = get_anthropic_client(api_key)
        self.prompt_service = get_prompt_service()

    def check_message(self, user_message: str) -> GuardrailResult:
        """Check whether a user message is allowed.

        Returns GuardrailResult(allowed=True/False, message=...).
        On any failure, returns allowed=False (fail closed).
        """
        # Fail closed: runtime errors (Braintrust outage, LLM errors) block
        # the message rather than accidentally letting unfiltered content through.
        try:
            system_prompt = self._load_prompt()
        except Exception as exc:
            logger.error(f"Guardrail: failed to load prompt: {exc}")
            return GuardrailResult(allowed=False)

        try:
            # Uses Haiku for speed/cost — classification doesn't need Sonnet.
            # temperature=0.0 for deterministic decisions.
            # output_config enforces JSON schema so we never get malformed output.
            response = self.client.messages.create(
                model=settings.GUARDRAIL_MODEL,
                max_tokens=256,
                temperature=0.0,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": GUARDRAIL_OUTPUT_SCHEMA,
                    }
                },
            )
            return self._parse_response(response)
        except Exception as exc:
            logger.error(f"Guardrail: LLM call failed: {exc}")
            return GuardrailResult(allowed=False)

    def _load_prompt(self) -> str:
        """Fetch the guardrail prompt from Braintrust via PromptService."""
        core_prompt = self.prompt_service.get_core_prompt(prompt_id=settings.GUARDRAIL_PROMPT_SLUG)
        return core_prompt.text

    def _parse_response(self, response) -> GuardrailResult:
        """Parse structured JSON response. Fail closed on unexpected output.

        The API guarantees valid JSON matching GUARDRAIL_OUTPUT_SCHEMA via
        output_config, so decision is always "ALLOW" or "BLOCK".
        """
        try:
            data = json.loads(response.content[0].text)
            return GuardrailResult(
                allowed=data["decision"] == "ALLOW",
                reason=data["reason"],
                message=data["message"],
            )
        except (json.JSONDecodeError, IndexError, KeyError) as exc:
            logger.warning(f"Guardrail: failed to parse response: {exc}")
            return GuardrailResult(allowed=False)


# Singleton — shared across requests within a process. The Anthropic client
# and prompt service are both safe to reuse, so this avoids per-request setup.
get_guardrail_service, reset_guardrail_service = make_singleton(GuardrailService)
