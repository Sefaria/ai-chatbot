"""
AI-based guardrail checker using LLM with Braintrust prompts.

This module provides AI-powered guardrail checking that can be updated
remotely via Braintrust prompts, with fallback to rule-based checking.
"""

import os
import json
import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from anthropic import Anthropic

from .reason_codes import ReasonCode
from .braintrust_client import get_braintrust_client

logger = logging.getLogger('chat.router.ai_guardrails')


@dataclass
class AIGuardrailResult:
    """Result from AI guardrail check."""
    allowed: bool = True
    decision: str = "ALLOW"  # ALLOW, BLOCK, WARN
    reason_codes: List[ReasonCode] = None
    refusal_message: Optional[str] = None
    confidence: float = 1.0
    reasoning: Optional[str] = None

    def __post_init__(self):
        if self.reason_codes is None:
            self.reason_codes = []


class AIGuardrailChecker:
    """
    AI-powered guardrail checker using Claude with Braintrust prompts.

    Features:
    - Uses Claude Haiku for fast, cost-effective classification
    - Prompts managed via Braintrust for remote updates
    - Fallback to rule-based checking if AI fails
    - Structured JSON output for reliable parsing
    """

    # Map AI reason codes to ReasonCode enum
    REASON_CODE_MAP = {
        "PROMPT_INJECTION": ReasonCode.GUARDRAIL_PROMPT_INJECTION,
        "SYSTEM_PROMPT_LEAK": ReasonCode.GUARDRAIL_SYSTEM_PROMPT_LEAK,
        "HARASSMENT": ReasonCode.GUARDRAIL_HARASSMENT,
        "HATE_SPEECH": ReasonCode.GUARDRAIL_HATE_SPEECH,
        "HIGH_RISK_PSAK": ReasonCode.GUARDRAIL_HIGH_RISK_PSAK,
        "MEDICAL_ADVICE": ReasonCode.GUARDRAIL_MEDICAL_ADVICE,
        "LEGAL_ADVICE": ReasonCode.GUARDRAIL_LEGAL_ADVICE,
        "PRIVACY_CONCERN": ReasonCode.GUARDRAIL_PRIVACY_REQUEST,
    }

    def __init__(
        self,
        model: str = "claude-3-5-haiku-20241022",
        prompt_version: str = "stable",
        fallback_checker: Optional[Any] = None,
    ):
        """
        Initialize the AI guardrail checker.

        Args:
            model: Claude model to use (default: haiku for speed/cost)
            prompt_version: Braintrust prompt version (default: "stable")
            fallback_checker: Optional rule-based checker for fallback
        """
        self.model = model
        self.prompt_version = prompt_version
        self.fallback_checker = fallback_checker

        # Initialize Anthropic client
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set")

        self.client = Anthropic(api_key=api_key)

        # Get Braintrust client
        self.braintrust_client = get_braintrust_client()

        logger.info(f"AI Guardrail Checker initialized with model={model}")

    def check(self, message: str, context: Optional[dict] = None) -> AIGuardrailResult:
        """
        Check a message against guardrails using AI.

        Args:
            message: The user message to check
            context: Optional context (conversation summary, user metadata, etc.)

        Returns:
            AIGuardrailResult with decision and reason codes
        """
        try:
            return self._check_with_ai(message, context)
        except Exception as e:
            logger.error(f"AI guardrail check failed: {e}")

            # Fall back to rule-based checker if available
            if self.fallback_checker:
                logger.info("Falling back to rule-based guardrail checker")
                rule_result = self.fallback_checker.check(message, context)
                return AIGuardrailResult(
                    allowed=rule_result.allowed,
                    decision="BLOCK" if not rule_result.allowed else "ALLOW",
                    reason_codes=rule_result.reason_codes,
                    refusal_message=rule_result.refusal_message,
                    confidence=rule_result.confidence,
                    reasoning="Fallback to rule-based checker due to AI failure"
                )

            # If no fallback, default to allowing (fail open for availability)
            logger.warning("No fallback checker available, defaulting to ALLOW")
            return AIGuardrailResult(
                allowed=True,
                decision="ALLOW",
                confidence=0.0,
                reasoning="Failed to check, defaulting to ALLOW"
            )

    def _check_with_ai(self, message: str, context: Optional[dict]) -> AIGuardrailResult:
        """Perform AI-based guardrail check."""
        # Get prompt from Braintrust
        prompt_template = self.braintrust_client.get_guardrail_prompt(self.prompt_version)

        # Format context
        context_str = json.dumps(context or {}, indent=2)

        # Format user prompt
        user_prompt = prompt_template.user_prompt_template.format(
            message=message,
            context=context_str
        )

        # Call Claude API
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            temperature=0.0,  # Deterministic for classification
            system=prompt_template.system_prompt,
            messages=[
                {"role": "user", "content": user_prompt}
            ]
        )

        # Parse response
        response_text = response.content[0].text.strip()
        logger.debug(f"AI guardrail response: {response_text}")

        # Try to parse as JSON
        try:
            # Extract JSON from response - handle various formats
            json_text = self._extract_json(response_text)
            result_data = json.loads(json_text)

            decision = result_data.get("decision", "ALLOW")
            confidence = result_data.get("confidence", 0.8)
            refusal_message = result_data.get("refusal_message")
            reasoning = result_data.get("reasoning", "")

            # Map reason codes
            reason_codes = []
            for code_str in result_data.get("reason_codes", []):
                if code_str in self.REASON_CODE_MAP:
                    reason_codes.append(self.REASON_CODE_MAP[code_str])

            # Determine allowed status
            allowed = decision == "ALLOW"

            # For WARN decisions, allow but keep reason codes
            if decision == "WARN":
                allowed = True

            return AIGuardrailResult(
                allowed=allowed,
                decision=decision,
                reason_codes=reason_codes,
                refusal_message=refusal_message,
                confidence=confidence,
                reasoning=reasoning
            )

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI response as JSON: {e}")
            logger.error(f"Response text: {response_text}")
            raise

    def _extract_json(self, text: str) -> str:
        """
        Extract JSON from AI response text.

        Handles various formats:
        - Plain JSON
        - JSON in markdown code blocks
        - JSON followed by explanation text
        """
        # Try markdown code blocks first
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        # Find JSON object boundaries
        # Look for first { and last } to extract just the JSON part
        start_idx = text.find('{')
        if start_idx == -1:
            return text

        # Find the matching closing brace
        brace_count = 0
        end_idx = start_idx
        for i in range(start_idx, len(text)):
            if text[i] == '{':
                brace_count += 1
            elif text[i] == '}':
                brace_count -= 1
                if brace_count == 0:
                    end_idx = i + 1
                    break

        return text[start_idx:end_idx]

    def check_batch(
        self,
        messages: List[str],
        contexts: Optional[List[dict]] = None
    ) -> List[AIGuardrailResult]:
        """
        Check multiple messages in batch.

        Args:
            messages: List of user messages to check
            contexts: Optional list of contexts (one per message)

        Returns:
            List of AIGuardrailResult objects
        """
        contexts = contexts or [None] * len(messages)
        results = []

        for message, context in zip(messages, contexts):
            result = self.check(message, context)
            results.append(result)

        return results


def get_ai_guardrail_checker(
    model: Optional[str] = None,
    prompt_version: str = "stable",
    fallback_checker: Optional[Any] = None,
) -> AIGuardrailChecker:
    """
    Create an AI guardrail checker instance.

    Args:
        model: Optional model override
        prompt_version: Braintrust prompt version
        fallback_checker: Optional rule-based checker for fallback

    Returns:
        AIGuardrailChecker instance
    """
    model = model or os.environ.get('GUARDRAIL_MODEL', 'claude-3-5-haiku-20241022')
    return AIGuardrailChecker(
        model=model,
        prompt_version=prompt_version,
        fallback_checker=fallback_checker
    )
