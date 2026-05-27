"""Fast topic appetizer — finds a relevant Sefaria topic within 5 seconds.

Haiku-first approach:
  Ask Haiku for up to 3 ranked topic candidates (short canonical names),
  then try each against the Sefaria name API until one matches.
  Multiple candidates improve accuracy and add natural "thinking time" (~2–4s).
All steps run inside a 5-second asyncio.wait_for timeout.
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

APPETIZER_TIMEOUT_SECONDS = 5
APPETIZER_MODEL = "claude-haiku-4-5-20251001"
SEFARIA_TOPICS_BASE_URL = "https://www.sefaria.org/topics"


@dataclass
class AppetizerResult:
    topic_slug: str
    topic_title: str
    topic_url: str
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
        metrics: dict = {"topic_found": None}
        t0 = time.monotonic()

        # Haiku returns up to 3 ranked candidates (handles any language)
        t1 = time.monotonic()
        candidates = await self._extract_candidates_via_haiku(user_message)
        metrics["haiku_ms"] = int((time.monotonic() - t1) * 1000)
        metrics["haiku_candidates"] = candidates
        logger.info("Appetizer candidates: %r from %r", candidates, user_message)

        for candidate in candidates:
            t2 = time.monotonic()
            result = await self._search_and_build(candidate)
            metrics.setdefault("searches", []).append(
                {
                    "candidate": candidate,
                    "ms": int((time.monotonic() - t2) * 1000),
                    "hit": result is not None,
                }
            )
            if result:
                logger.info("Appetizer hit for candidate=%r", candidate)
                metrics["topic_found"] = result.topic_slug
                metrics["total_ms"] = int((time.monotonic() - t0) * 1000)
                result.metrics = metrics
                return result

        metrics["total_ms"] = int((time.monotonic() - t0) * 1000)
        logger.info("Appetizer: no result from any candidate, metrics=%s", metrics)
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

    async def _extract_candidates_via_haiku(self, user_message: str) -> list[str]:
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
                    "If there is no clear topic (e.g. greetings, technical questions), "
                    "return NONE."
                ),
                messages=[{"role": "user", "content": user_message}],
            )
            text = response.content[0].text.strip()
            if not text or text.upper().rstrip(".,!?") == "NONE":
                return []
            candidates = [c.strip() for c in text.split(",") if c.strip()]
            return candidates[:3]
        except Exception:
            logger.exception("Haiku candidate extraction failed")
            return []


get_appetizer_service, reset_appetizer_service = make_singleton(AppetizerService)
