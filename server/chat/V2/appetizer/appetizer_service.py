"""Topic appetizer — finds relevant Sefaria topics within 8 seconds.

Sonnet extracts up to 3 ranked topic candidates (short canonical names),
then we try each against the Sefaria name API, collecting all matches.
All steps run inside an 8-second asyncio.wait_for timeout.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from ..agent.sefaria_client import SefariaClient
from ..pricing import tracked_messages_create
from ..utils import get_anthropic_client, make_singleton

logger = logging.getLogger("chat.appetizer")

APPETIZER_TIMEOUT_SECONDS = 8
APPETIZER_MODEL = "claude-haiku-4-5-20251001"
SEFARIA_TOPICS_BASE_URL = "https://www.sefaria.org/topics"


@dataclass
class TopicInfo:
    topic_slug: str
    topic_title: str
    topic_url: str


@dataclass
class AppetizerResult:
    topics: list[TopicInfo]
    metrics: dict = field(default_factory=dict)


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
        metrics: dict = {"topics_found": []}
        t0 = time.monotonic()

        t1 = time.monotonic()
        candidates = await self._extract_candidates_via_llm(user_message)
        metrics["llm_ms"] = int((time.monotonic() - t1) * 1000)
        metrics["llm_candidates"] = candidates
        logger.info("Appetizer candidates: %r from %r", candidates, user_message)

        topics: list[TopicInfo] = []
        seen_slugs: set[str] = set()

        for candidate in candidates:
            t2 = time.monotonic()
            topic_info = await self._search_and_build(candidate)
            metrics.setdefault("searches", []).append(
                {
                    "candidate": candidate,
                    "ms": int((time.monotonic() - t2) * 1000),
                    "hit": topic_info is not None,
                }
            )
            if topic_info and topic_info.topic_slug not in seen_slugs:
                seen_slugs.add(topic_info.topic_slug)
                topics.append(topic_info)

        metrics["topics_found"] = [t.topic_slug for t in topics]
        metrics["total_ms"] = int((time.monotonic() - t0) * 1000)

        if not topics:
            logger.info("Appetizer: no result from any candidate, metrics=%s", metrics)
            return None

        logger.info("Appetizer: found %d topics, metrics=%s", len(topics), metrics)
        return AppetizerResult(topics=topics, metrics=metrics)

    async def _search_and_build(self, query: str) -> TopicInfo | None:
        logger.info("Appetizer: searching for topics with query=%r", query)
        topics = await self.sefaria_client.search_topics(query, limit=3)
        logger.info(
            "Appetizer: search_topics returned %d topics: %s",
            len(topics) if topics else 0,
            topics,
        )
        if not topics:
            return None
        best = topics[0]
        return TopicInfo(
            topic_slug=best["slug"],
            topic_title=best["title"],
            topic_url=f"{SEFARIA_TOPICS_BASE_URL}/{best['slug']}",
        )

    async def _extract_candidates_via_llm(self, user_message: str) -> list[str]:
        """Return up to 3 ranked topic candidates from the user's message."""
        try:
            response = await asyncio.to_thread(
                tracked_messages_create,
                self.client,
                model=APPETIZER_MODEL,
                max_tokens=80,
                temperature=0.0,
                system=(
                    "Identify up to 3 topics, concepts, or figures from the user's "
                    "question that could appear in the Sefaria library — a Jewish text "
                    "library covering Torah, Talmud, halacha, and all areas of Jewish "
                    "thought including universal topics like parenting, money, "
                    "relationships, health, and ethics. "
                    "Return a comma-separated list, most relevant first. "
                    "Use the shortest canonical English name for each "
                    "(e.g. 'Herod' not 'Herod the Great', 'Moses' not 'Moses our Teacher'). "
                    "Prefer specific named topics over abstract meta-topics "
                    "(e.g. 'Shabbat' over 'Jewish Law', 'Moses' over 'Leadership'). "
                    "Each candidate should be distinct — don't return near-synonyms. "
                    "If there is no clear topic (e.g. greetings, technical questions), "
                    "return NONE."
                ),
                messages=[{"role": "user", "content": user_message}],
            )
            text = response.content[0].text.strip()
            logger.info("Appetizer LLM raw response: %r for prompt: %r", text, user_message)
            if not text or text.upper().rstrip(".,!?") == "NONE":
                logger.info("Appetizer LLM decided: no topic (NONE) for prompt: %r", user_message)
                return []
            candidates = [c.strip() for c in text.split(",") if c.strip()]
            logger.info(
                "Appetizer LLM decided: candidates=%r for prompt: %r", candidates, user_message
            )
            return candidates[:3]
        except Exception:
            logger.exception("LLM candidate extraction failed")
            return []


get_appetizer_service, reset_appetizer_service = make_singleton(AppetizerService)
