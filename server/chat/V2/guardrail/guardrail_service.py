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
from ..prompts.prompt_fragments import GUARDRAIL_MALFORMED_REASON, GUARDRAIL_UNAVAILABLE_REASON
from ..utils import get_anthropic_client, make_singleton, strip_markdown_fences

logger = logging.getLogger("chat.guardrail")


@dataclass
class GuardrailResult:
    """Result of a guardrail check."""

    allowed: bool
    reason: str = ""


def parse_guardrail_response(text: str) -> GuardrailResult:
    """Parse guardrail LLM response text into a GuardrailResult. Fail closed on malformed output.

    Expected format: {decision: "ALLOW"/"BLOCK", reason: "..."}
    Text may be wrapped in markdown code fences (```json ... ```).
    Only an explicit "ALLOW" passes — any other decision value is a block.
    """
    try:
        cleaned = strip_markdown_fences(text)
        data = json.loads(cleaned)

        decision = data.get("decision", "")
        if not decision:
            logger.warning(f"Guardrail: missing 'decision' field: {cleaned[:200]}")
            return GuardrailResult(allowed=False, reason=GUARDRAIL_MALFORMED_REASON)

        allowed = decision.upper() == "ALLOW"
        return GuardrailResult(allowed=allowed, reason=data.get("reason", ""))
    except (json.JSONDecodeError, IndexError, KeyError) as exc:
        logger.warning(f"Guardrail: failed to parse response: {exc}")
        return GuardrailResult(allowed=False, reason=GUARDRAIL_MALFORMED_REASON)


class GuardrailService:
    """Classifies user messages as allowed or blocked using an LLM filter.

    Uses a Braintrust-managed prompt (guardrail-checker) as the system prompt,
    sends the user message, and expects JSON {decision, reason} back.
    Fails closed on any error.
    """

    def __init__(self, api_key: str | None = None):
        self.client = get_anthropic_client(api_key)
        self.prompt_service = get_prompt_service()

    def check_message(self, user_message: str) -> GuardrailResult:
        """Check whether a user message is allowed.

        Returns GuardrailResult(allowed=True/False, reason=...).
        On any failure, returns allowed=False (fail closed).
        """
        # Fail closed: runtime errors (Braintrust outage, LLM errors) block
        # the message rather than accidentally letting unfiltered content through.
        try:
            system_prompt = self._load_prompt()
        except Exception as exc:
            logger.error(f"Guardrail: failed to load prompt: {exc}")
            return GuardrailResult(allowed=False, reason=GUARDRAIL_UNAVAILABLE_REASON)

        try:
            # Uses Haiku for speed/cost — classification doesn't need Sonnet.
            # temperature=0.0 for deterministic decisions.
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
            return GuardrailResult(allowed=False, reason=GUARDRAIL_UNAVAILABLE_REASON)

    def _load_prompt(self) -> str:
        """Fetch the guardrail prompt from Braintrust via PromptService."""
        core_prompt = self.prompt_service.get_core_prompt(prompt_id=settings.GUARDRAIL_PROMPT_SLUG)
        return core_prompt.text

    def _parse_response(self, response) -> GuardrailResult:
        """Parse Anthropic Messages response. Delegates to parse_guardrail_response."""
        return parse_guardrail_response(response.content[0].text)


# Singleton — shared across requests within a process. The Anthropic client
# and prompt service are both safe to reuse, so this avoids per-request setup.
get_guardrail_service, reset_guardrail_service = make_singleton(GuardrailService)
