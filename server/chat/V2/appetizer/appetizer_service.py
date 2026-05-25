"""Fast topic appetizer — finds a relevant Sefaria topic within 5 seconds.

Two-tier approach:
  Tier 1: Strip prompt wrappers, search Sefaria name API directly (<500ms)
  Tier 2: If no topic found, use Haiku to extract the concept, retry (2-4s)
Both tiers run inside a 5-second asyncio.wait_for timeout.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from ..agent.sefaria_client import SefariaClient
from ..pricing import tracked_messages_create
from ..utils import get_anthropic_client, make_singleton

logger = logging.getLogger("chat.appetizer")

APPETIZER_TIMEOUT_SECONDS = 5
APPETIZER_MODEL = "claude-haiku-4-5-20251001"
SEFARIA_TOPICS_BASE_URL = "https://www.sefaria.org/topics"

_STRIP_PREFIXES = [
    r"(?:can you |please )?(?:find|show|give|get|bring)(?: me)? (?:some )?(?:sources?|texts?|references?) (?:about|on|for|regarding|related to) ",
    r"(?:can you |please )?(?:tell|teach) me (?:about|more about) ",
    r"what (?:does |do |did )?(?:the )?(?:torah|talmud|bible|tanakh|mishnah|gemara) (?:say|teach)s? (?:about|on|regarding) ",
    r"what (?:is|are|was|were) (?:the )?",
]
_STRIP_RE = re.compile(
    r"^(?:" + "|".join(_STRIP_PREFIXES) + r")",
    re.IGNORECASE,
)


def _extract_query_words(prompt: str) -> str:
    """Strip common natural-language prompt wrappers, return the topical core."""
    prompt = prompt.strip()
    if not prompt:
        return ""
    cleaned = _STRIP_RE.sub("", prompt).strip().rstrip("?.,!")
    return cleaned if cleaned else prompt


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
        # Tier 1: direct keyword search (<500ms)
        query = _extract_query_words(user_message)
        logger.info("Appetizer tier-1 query extracted: %r from %r", query, user_message)
        if query:
            result = await self._search_and_build(query)
            logger.info("Appetizer tier-1 search result: %s", result)
            if result:
                logger.info("Appetizer tier-1 hit for query=%r", query)
                return result

        # Tier 2: Haiku concept extraction fallback (2-4s)
        concept = await self._extract_concept_via_haiku(user_message)
        logger.info("Appetizer tier-2 concept extracted: %r", concept)
        if concept:
            result = await self._search_and_build(concept)
            logger.info("Appetizer tier-2 search result: %s", result)
            if result:
                logger.info("Appetizer tier-2 hit for concept=%r", concept)
                return result

        logger.info("Appetizer: no result from either tier")
        return None

    async def _search_and_build(self, query: str) -> AppetizerResult | None:
        logger.info("Appetizer: searching for topics with query=%r", query)
        topics = await self.sefaria_client.search_topics(query, limit=3)
        logger.info(
            "Appetizer: search_topics returned %d topics: %s", len(topics) if topics else 0, topics
        )
        if not topics:
            return None
        best = topics[0]
        return AppetizerResult(
            topic_slug=best["slug"],
            topic_title=best["title"],
            topic_url=f"{SEFARIA_TOPICS_BASE_URL}/{best['slug']}",
        )

    async def _extract_concept_via_haiku(self, user_message: str) -> str | None:
        try:
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
        except Exception:
            logger.exception("Haiku concept extraction failed")
            return None


get_appetizer_service, reset_appetizer_service = make_singleton(AppetizerService)
