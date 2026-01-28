"""
AI-based flow router using LLM with Braintrust prompts.

This module provides AI-powered flow classification that can be updated
remotely via Braintrust prompts, with fallback to rule-based routing.
"""

import json
import logging
import os
from enum import Enum
from typing import Any

from anthropic import Anthropic

from .braintrust_client import get_braintrust_client
from .reason_codes import ReasonCode

logger = logging.getLogger("chat.router.ai_router")


class Flow(str, Enum):
    """Conversation flow types."""

    TRANSLATION = "TRANSLATION"
    DISCOVERY = "DISCOVERY"
    DEEP_ENGAGEMENT = "DEEP_ENGAGEMENT"
    REFUSE = "REFUSE"


class AIFlowRouter:
    """
    AI-powered flow router using Claude with Braintrust prompts.

    Features:
    - Uses Claude Haiku for fast, cost-effective classification
    - Prompts managed via Braintrust for remote updates
    - Fallback to rule-based routing if AI fails
    - Structured JSON output for reliable parsing
    """

    # Map AI reason codes to ReasonCode enum
    REASON_CODE_MAP = {
        "TRANSLATION_KEYWORDS": ReasonCode.ROUTE_TRANSLATION_KEYWORDS,
        "TRANSLATION_REQUEST": ReasonCode.ROUTE_TRANSLATION_REQUEST,
        "TRANSLATION_INTENT": ReasonCode.ROUTE_TRANSLATION_INTENT,
        "DISCOVERY_KEYWORDS": ReasonCode.ROUTE_DISCOVERY_KEYWORDS,
        "DISCOVERY_REFERENCE_REQUEST": ReasonCode.ROUTE_DISCOVERY_REFERENCE_REQUEST,
        "DISCOVERY_INTENT": ReasonCode.ROUTE_DISCOVERY_INTENT,
        "DEEP_ENGAGEMENT_LEARNING": ReasonCode.ROUTE_DEEP_ENGAGEMENT_LEARNING,
        "DEEP_ENGAGEMENT_EXPLANATION": ReasonCode.ROUTE_DEEP_ENGAGEMENT_EXPLANATION,
        "DEEP_ENGAGEMENT_INTENT": ReasonCode.ROUTE_DEEP_ENGAGEMENT_INTENT,
        "FLOW_STICKINESS": ReasonCode.ROUTE_FLOW_STICKINESS,
        "DEFAULT_DEEP_ENGAGEMENT": ReasonCode.ROUTE_DEFAULT_DEEP_ENGAGEMENT,
    }

    def __init__(
        self,
        model: str = "claude-3-5-haiku-20241022",
        prompt_version: str = "stable",
        fallback_classifier: Any | None = None,
    ):
        """
        Initialize the AI flow router.

        Args:
            model: Claude model to use (default: haiku for speed/cost)
            prompt_version: Braintrust prompt version (default: "stable")
            fallback_classifier: Optional rule-based classifier for fallback
        """
        self.model = model
        self.prompt_version = prompt_version
        self.fallback_classifier = fallback_classifier

        # Initialize Anthropic client
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set")

        self.client = Anthropic(api_key=api_key)

        # Get Braintrust client
        self.braintrust_client = get_braintrust_client()

        logger.info(f"AI Flow Router initialized with model={model}")

    def classify(
        self,
        message: str,
        conversation_summary: str = "",
        previous_flow: str | None = None,
    ) -> tuple[Flow, float, list[ReasonCode]]:
        """
        Classify user message into a flow using AI.

        Args:
            message: The user's message
            conversation_summary: Rolling summary of conversation
            previous_flow: The flow from the previous turn (for stickiness)

        Returns:
            Tuple of (flow, confidence, reason_codes)
        """
        try:
            return self._classify_with_ai(message, conversation_summary, previous_flow)
        except Exception as e:
            logger.error(f"AI flow classification failed: {e}")

            # Fall back to rule-based classifier if available
            if self.fallback_classifier:
                logger.info("Falling back to rule-based flow classifier")
                return self.fallback_classifier(message, conversation_summary, previous_flow)

            # If no fallback, default to deep engagement
            logger.warning("No fallback classifier available, defaulting to DEEP_ENGAGEMENT flow")
            return Flow.DEEP_ENGAGEMENT, 0.5, [ReasonCode.ROUTE_DEFAULT_DEEP_ENGAGEMENT]

    def _classify_with_ai(
        self,
        message: str,
        conversation_summary: str,
        previous_flow: str | None,
    ) -> tuple[Flow, float, list[ReasonCode]]:
        """Perform AI-based flow classification."""
        # Get prompt from Braintrust
        prompt_template = self.braintrust_client.get_router_prompt(self.prompt_version)

        # Format user prompt
        user_prompt = prompt_template.user_prompt_template.format(
            message=message,
            conversation_summary=conversation_summary or "None",
            previous_flow=previous_flow or "None",
        )

        # Call Claude API
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            temperature=0.0,  # Deterministic for classification
            system=prompt_template.system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        # Parse response
        response_text = response.content[0].text.strip()
        logger.debug(f"AI router response: {response_text}")

        # Try to parse as JSON
        try:
            # Extract JSON from response - handle various formats
            json_text = self._extract_json(response_text)
            result_data = json.loads(json_text)

            # Extract flow
            flow_str = result_data.get("flow", "DEEP_ENGAGEMENT")
            try:
                flow = Flow[flow_str]
            except KeyError:
                logger.warning(f"Unknown flow '{flow_str}', defaulting to DEEP_ENGAGEMENT")
                flow = Flow.DEEP_ENGAGEMENT

            # Extract confidence
            confidence = result_data.get("confidence", 0.8)

            # Map reason codes
            reason_codes = []
            for code_str in result_data.get("reason_codes", []):
                if code_str in self.REASON_CODE_MAP:
                    reason_codes.append(self.REASON_CODE_MAP[code_str])

            # Log reasoning if available
            reasoning = result_data.get("reasoning")
            if reasoning:
                logger.debug(f"AI routing reasoning: {reasoning}")

            return flow, confidence, reason_codes

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
        start_idx = text.find("{")
        if start_idx == -1:
            return text

        # Find the matching closing brace
        brace_count = 0
        end_idx = start_idx
        for i in range(start_idx, len(text)):
            if text[i] == "{":
                brace_count += 1
            elif text[i] == "}":
                brace_count -= 1
                if brace_count == 0:
                    end_idx = i + 1
                    break

        return text[start_idx:end_idx]

    def classify_batch(
        self,
        messages: list[str],
        conversation_summaries: list[str] | None = None,
        previous_flows: list[str | None] | None = None,
    ) -> list[tuple[Flow, float, list[ReasonCode]]]:
        """
        Classify multiple messages in batch.

        Args:
            messages: List of user messages to classify
            conversation_summaries: Optional list of conversation summaries
            previous_flows: Optional list of previous flows

        Returns:
            List of (flow, confidence, reason_codes) tuples
        """
        conversation_summaries = conversation_summaries or [""] * len(messages)
        previous_flows = previous_flows or [None] * len(messages)

        results = []
        for message, summary, prev_flow in zip(
            messages, conversation_summaries, previous_flows, strict=False
        ):
            result = self.classify(message, summary, prev_flow)
            results.append(result)

        return results


def get_ai_flow_router(
    model: str | None = None,
    prompt_version: str = "stable",
    fallback_classifier: Any | None = None,
) -> AIFlowRouter:
    """
    Create an AI flow router instance.

    Args:
        model: Optional model override
        prompt_version: Braintrust prompt version
        fallback_classifier: Optional rule-based classifier for fallback

    Returns:
        AIFlowRouter instance
    """
    model = model or os.environ.get("ROUTER_MODEL", "claude-3-5-haiku-20241022")
    return AIFlowRouter(
        model=model, prompt_version=prompt_version, fallback_classifier=fallback_classifier
    )
