"""
Router service — post-guardrail classifier that routes messages to different prompts.

Classifies user messages as Translation, Discovery, or Other using an LLM.
- Translation → uses a dedicated translation prompt
- Discovery → uses the current core prompt (default behavior)
- Other → rewrites the message into a Discovery question, then uses core prompt

Fails open: any error → default to Discovery (guardrail already validated the input).
"""

import json
import logging
from dataclasses import dataclass
from enum import Enum

from django.conf import settings

from ..prompts import get_prompt_service
from ..utils import get_anthropic_client, make_singleton, strip_markdown_fences

logger = logging.getLogger("chat.router")


class RouteType(Enum):
    TRANSLATION = "translation"
    DISCOVERY = "discovery"
    OTHER = "other"


@dataclass
class RouterResult:
    """Result of routing classification."""

    route: RouteType
    core_prompt_id: str | None = None
    rewritten_message: str | None = None


class RouterService:
    """Classifies user messages and routes them to the appropriate prompt.

    Uses a Braintrust-managed prompt (router-classifier) as the system prompt,
    sends the user message, and expects JSON {route, reason} back.
    Fails open to Discovery on any error.
    """

    def __init__(self, api_key: str | None = None):
        self.client = get_anthropic_client(api_key)
        self.prompt_service = get_prompt_service()

    def classify(self, user_message: str) -> RouterResult:
        """Classify a user message into a route and return the appropriate prompt ID.

        Returns RouterResult with route type, prompt ID, and optional rewritten message.
        On any failure, returns Discovery route (fail open).
        """
        try:
            route = self._classify_message(user_message)
        except Exception as exc:
            logger.error(f"Router: classification failed: {exc}")
            return RouterResult(route=RouteType.DISCOVERY)

        if route == RouteType.TRANSLATION:
            return RouterResult(
                route=RouteType.TRANSLATION,
                core_prompt_id=settings.TRANSLATION_PROMPT_SLUG,
            )

        if route == RouteType.OTHER:
            # rewritten = self._rewrite_message(user_message)
            return RouterResult(
                route=RouteType.DISCOVERY,
                # rewritten_message=rewritten,
            )

        # Discovery — use default core prompt (None means caller keeps its default)
        return RouterResult(route=RouteType.DISCOVERY)

    @staticmethod
    def _deterministic_classify(user_message: str) -> RouteType | None:
        """
        Deterministic classification for testing/debugging without LLM calls.
        Currently checks if the first three words contain "translate" to route to Translation.
        """
        import re

        user_message_words = re.findall(r"\w+", user_message.lower())
        first_three_words = " ".join(user_message_words[:3])
        if re.search(r"\btranslate\b", first_three_words, re.IGNORECASE):
            logger.info("Deterministic classification successful. Routing to Translation.")
            return RouteType.TRANSLATION
        return None

    def _classify_message(self, user_message: str) -> RouteType:
        """Call LLM to classify the message. Raises on failure."""
        if deterministic_route := self._deterministic_classify(user_message):
            return deterministic_route
        system_prompt = self._load_prompt(settings.ROUTER_PROMPT_SLUG)

        response = self.client.messages.create(
            model=settings.ROUTER_MODEL,
            max_tokens=256,
            temperature=0.0,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        return self._parse_classification(response)

    def _rewrite_message(self, user_message: str) -> str | None:
        """Rewrite a message into a Discovery-style question. Returns None on failure."""
        try:
            system_prompt = self._load_prompt(settings.REWRITER_PROMPT_SLUG)

            response = self.client.messages.create(
                model=settings.ROUTER_MODEL,
                max_tokens=512,
                temperature=0.0,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )

            rewritten = response.content[0].text.strip()
            if rewritten:
                return rewritten
            logger.warning("Router: rewriter returned empty text")
            return None
        except Exception as exc:
            logger.error(f"Router: rewrite failed: {exc}")
            return None

    def _load_prompt(self, slug: str) -> str:
        """Fetch a prompt from Braintrust via PromptService."""
        core_prompt = self.prompt_service.get_core_prompt(prompt_id=slug)
        return core_prompt.text

    def _parse_classification(self, response) -> RouteType:
        """Parse LLM response JSON. Fail open to Discovery on malformed output.

        Expected format: {route: "translation"/"discovery"/"other", reason: "..."}
        """
        text = strip_markdown_fences(response.content[0].text)
        data = json.loads(text)

        route_value = data.get("route", "").lower()
        if not route_value:
            logger.warning(f"Router: missing 'route' field: {text[:200]}")
            return RouteType.DISCOVERY

        route_map = {
            "translation": RouteType.TRANSLATION,
            "discovery": RouteType.DISCOVERY,
            "other": RouteType.OTHER,
        }

        route = route_map.get(route_value)
        if route is None:
            logger.warning(f"Router: unknown route value '{route_value}'")
            return RouteType.DISCOVERY

        return route


get_router_service, reset_router_service = make_singleton(RouterService)
