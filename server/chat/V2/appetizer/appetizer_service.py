"""Fast topic appetizer — finds a relevant Sefaria topic within 5 seconds."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from ..agent.sefaria_client import SefariaClient
from ..pricing import tracked_messages_create
from ..utils import get_anthropic_client, make_singleton

logger = logging.getLogger("chat.appetizer")

APPETIZER_TIMEOUT_SECONDS = 5
APPETIZER_MODEL = "claude-haiku-4-5-20251001"
SEFARIA_TOPICS_BASE_URL = "https://www.sefaria.org/topics"


@dataclass
class AppetizerResult:
    topic_slug: str
    topic_title: str
    topic_url: str


class AppetizerService:
    def __init__(self):
        self.client = get_anthropic_client()
        self.sefaria_client = SefariaClient()

    async def find_appetizer(self, user_message: str) -> AppetizerResult | None:
        try:
            return await asyncio.wait_for(
                self._find_appetizer_inner(user_message),
                timeout=APPETIZER_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning("Appetizer timed out after %ds", APPETIZER_TIMEOUT_SECONDS)
            return None
        except Exception:
            logger.exception("Appetizer failed")
            return None

    async def _find_appetizer_inner(self, user_message: str) -> AppetizerResult | None:
        concept = await self._extract_concept(user_message)
        if not concept:
            return None

        topics = await self.sefaria_client.search_topics(concept, limit=3)
        if not topics:
            return None

        best = topics[0]
        return AppetizerResult(
            topic_slug=best["slug"],
            topic_title=best["title"],
            topic_url=f"{SEFARIA_TOPICS_BASE_URL}/{best['slug']}",
        )

    async def _extract_concept(self, user_message: str) -> str | None:
        response = await asyncio.to_thread(
            tracked_messages_create,
            self.client,
            model=APPETIZER_MODEL,
            max_tokens=50,
            temperature=0.0,
            system=(
                "Extract the single most important Jewish topic, concept, or figure "
                "from the user's question. Return ONLY the topic name in English, "
                "nothing else. If the question is not about Jewish texts/topics, "
                "return NONE."
            ),
            messages=[{"role": "user", "content": user_message}],
        )
        text = response.content[0].text.strip()
        if not text or text.upper().rstrip(".,!?") == "NONE":
            return None
        return text


get_appetizer_service, reset_appetizer_service = make_singleton(AppetizerService)
