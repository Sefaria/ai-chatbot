"""Topic appetizer — finds relevant Sefaria topics within 5 seconds.

Pipeline (in priority order):
1. Daf Yomi / recent-dapim intent → suppress (refs can't be shown as topics).
2. Current-parsha intent → calendar API lookup, no LLM needed.
3. All other prompts → Sonnet extracts up to 3 candidates, filtered through a
   generic-term blocklist, then looked up against Sefaria in parallel.

The outer asyncio.wait_for hard-cap is 5 seconds.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from django.conf import settings

from ..agent.sefaria_client import SefariaClient
from ..pricing import tracked_messages_create
from ..utils import get_anthropic_client, make_singleton
from .calendar_context import render_calendar_context

logger = logging.getLogger("chat.appetizer")

APPETIZER_TIMEOUT_SECONDS = 5
SEFARIA_TOPICS_BASE_URL = "https://www.sefaria.org/topics"

# ---------------------------------------------------------------------------
# Intent patterns
# ---------------------------------------------------------------------------

_PARSHA_INTENT_RE = re.compile(
    r"\b("
    r"this\s+week.s?\s+(parsha|parashat|torah\s+portion|portion)"
    r"|current\s+(parsha|parashat|torah\s+portion)"
    r"|weekly\s+(parsha|parashat|portion|torah\s+reading)"
    r"|parshat?\s+hashavua"
    r"|parashat\s+hashavua"
    r"|this\s+shabbat.s?\s+(parsha|portion)"
    r")",
    re.IGNORECASE,
)

# Daf Yomi and "recent dapim" style queries — no useful library topic exists for
# the current daf, so suppress rather than return an unrelated topic.
_DAF_YOMI_SUPPRESS_RE = re.compile(
    r"\b("
    r"daf[\s\-]yomi"
    r"|last\s+few\s+dap[im]+"
    r"|recent\s+dap[im]+"
    r"|today.s?\s+daf"
    r"|this\s+week.s?\s+daf"
    r")",
    re.IGNORECASE,
)

# Generic / schedule-descriptor terms the LLM sometimes emits that produce
# noisy or meta topics rather than specific content topics.
_GENERIC_BLOCKLIST: frozenset[str] = frozenset(
    {
        "parsha",
        "parashat",
        "torah reading",
        "torah portion",
        "torah portions",
        "daf yomi",
        "recent dapim",
        "recent daf",
        "hashavua",
        "parashat hashavua",
        "weekly parsha",
        "weekly portion",
    }
)

TOPIC_EXTRACTION_TOOL = {
    "name": "extract_topics",
    "description": (
        "Extract topic names from the user's message that could appear in the Sefaria library."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "topics": {
                "type": "string",
                "description": (
                    "Return up to 3 comma-separated Sefaria topic names, most relevant first. "
                    "Use the shortest canonical English name for each "
                    "(e.g. 'Herod' not 'Herod the Great', 'Moses' not 'Moses our Teacher'). "
                    "Prefer specific named topics over broad meta-topics "
                    "(e.g. 'Shabbat' over 'Jewish Law', 'Moses' over 'Leadership'). "
                    "Each candidate must be distinct — avoid near-synonyms. "
                    "Return 'NONE' for schedule/calendar questions (current parsha, daf yomi, "
                    "today's learning) or when there is genuinely no topic (greetings, "
                    "technical questions). "
                    "GOOD: 'Shabbat, Kiddush, Havdalah' | BAD: 'Parsha, Torah, Jewish Law'"
                ),
            },
        },
        "required": ["topics"],
    },
}


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
        self._calendar_cache: tuple[str, str] | None = None

    async def _get_calendar_context(self) -> str:
        """Compact calendar block, fetched at most once per day per process."""
        today = datetime.now().date().isoformat()
        cache = getattr(self, "_calendar_cache", None)
        if cache and cache[0] == today:
            return cache[1]
        try:
            calendar = await self.sefaria_client.get_current_calendar()
            rendered = render_calendar_context(calendar)
        except Exception:
            logger.exception("Appetizer: calendar fetch failed")
            rendered = "<calendar_context>unavailable</calendar_context>"
        self._calendar_cache = (today, rendered)
        return rendered

    async def find_appetizer(
        self,
        user_message: str,
        interface_lang: str = "en",
    ) -> AppetizerResult | None:
        use_hebrew = interface_lang == "he"
        try:
            return await asyncio.wait_for(
                self._find_appetizer_inner(user_message, use_hebrew=use_hebrew),
                timeout=APPETIZER_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning("Appetizer timed out after %ds", APPETIZER_TIMEOUT_SECONDS)
            return None
        except Exception:
            logger.exception("Appetizer failed")
            return None

    async def _find_appetizer_inner(
        self, user_message: str, use_hebrew: bool = False
    ) -> AppetizerResult | None:
        metrics: dict = {"topics_found": []}
        t0 = time.monotonic()

        # --- Intent gate 1: Daf Yomi / recent dapim → suppress ---
        if _DAF_YOMI_SUPPRESS_RE.search(user_message):
            logger.info("Appetizer: suppressed (daf yomi/recent dapim intent): %r", user_message)
            metrics["suppressed"] = True
            metrics["reason"] = "daf_yomi_suppress"
            metrics["total_ms"] = int((time.monotonic() - t0) * 1000)
            return None

        # --- Intent gate 2: Current parsha → calendar path (no LLM) ---
        if _PARSHA_INTENT_RE.search(user_message):
            logger.info("Appetizer: using calendar path for parsha intent: %r", user_message)
            topics, cal_metrics = await self._handle_parsha_intent(use_hebrew=use_hebrew)
            metrics.update(cal_metrics)
            metrics["topics_found"] = [t.topic_slug for t in topics]
            metrics["total_ms"] = int((time.monotonic() - t0) * 1000)
            if not topics:
                return None
            return AppetizerResult(topics=topics, metrics=metrics)

        # --- Normal LLM path ---
        t1 = time.monotonic()
        all_candidates = await self._extract_candidates_via_llm(user_message)
        metrics["llm_ms"] = int((time.monotonic() - t1) * 1000)
        metrics["llm_raw_candidates"] = all_candidates

        # Filter generic / schedule-descriptor terms
        candidates = [c for c in all_candidates if c.lower() not in _GENERIC_BLOCKLIST]
        suppressed_generic = [c for c in all_candidates if c.lower() in _GENERIC_BLOCKLIST]
        if suppressed_generic:
            logger.info(
                "Appetizer: suppressed generic candidates %r from %r",
                suppressed_generic,
                user_message,
            )
            metrics["suppressed_generic"] = suppressed_generic
        metrics["llm_candidates"] = candidates
        logger.info("Appetizer candidates: %r from %r", candidates, user_message)

        if not candidates:
            metrics["topics_found"] = []
            metrics["total_ms"] = int((time.monotonic() - t0) * 1000)
            return None

        # Parallel topic lookups — faster than sequential, fits within 5s budget
        t_searches = time.monotonic()
        search_results = await asyncio.gather(
            *[self._search_and_build(candidate, use_hebrew=use_hebrew) for candidate in candidates]
        )
        metrics["searches_ms"] = int((time.monotonic() - t_searches) * 1000)

        topics: list[TopicInfo] = []
        seen_slugs: set[str] = set()
        for candidate, topic_info in zip(candidates, search_results, strict=False):
            metrics.setdefault("searches", []).append(
                {"candidate": candidate, "hit": topic_info is not None}
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

    async def _handle_parsha_intent(self, use_hebrew: bool = False) -> tuple[list[TopicInfo], dict]:
        """Resolve current-parsha topics via the calendar API (no LLM call)."""
        metrics: dict = {"intent": "current_parsha"}
        t0 = time.monotonic()
        try:
            calendar = await self.sefaria_client.get_current_calendar()
            parsha_item = next(
                (
                    item
                    for item in calendar.get("calendar_items", [])
                    if item.get("title", {}).get("en") == "Parashat Hashavua"
                ),
                None,
            )
            if not parsha_item:
                logger.info("Appetizer: no Parashat Hashavua in calendar response")
                metrics["suppressed"] = True
                metrics["reason"] = "no_parsha_in_calendar"
                return [], metrics

            display = parsha_item.get("displayValue", {}).get("en", "")
            # "Chukat-Balak" → ["Parashat Chukat", "Parashat Balak"]
            portion_names = [f"Parashat {p.strip()}" for p in display.split("-") if p.strip()]
            logger.info(
                "Appetizer: parsha portions=%r from displayValue=%r", portion_names, display
            )

            search_results = await asyncio.gather(
                *[self._search_and_build(name, use_hebrew=use_hebrew) for name in portion_names]
            )

            topics: list[TopicInfo] = []
            seen_slugs: set[str] = set()
            for topic_info in search_results:
                if topic_info and topic_info.topic_slug not in seen_slugs:
                    seen_slugs.add(topic_info.topic_slug)
                    topics.append(topic_info)

            metrics["total_ms"] = int((time.monotonic() - t0) * 1000)
            return topics, metrics

        except Exception:
            logger.exception("Appetizer: parsha calendar lookup failed")
            metrics["error"] = True
            metrics["total_ms"] = int((time.monotonic() - t0) * 1000)
            return [], metrics

    async def _search_and_build(self, query: str, use_hebrew: bool = False) -> TopicInfo | None:
        logger.info("Appetizer: searching for topics with query=%r", query)
        topics = await self.sefaria_client.search_topics(query, limit=3, pool="library")
        logger.info(
            "Appetizer: search_topics returned %d topics: %s",
            len(topics) if topics else 0,
            topics,
        )
        if not topics:
            return None
        best = topics[0]
        title = best.get("he", "") if use_hebrew else best.get("title", "")
        return TopicInfo(
            topic_slug=best["slug"],
            topic_title=title,
            topic_url=f"{SEFARIA_TOPICS_BASE_URL}/{best['slug']}",
        )

    async def _extract_candidates_via_llm(self, user_message: str) -> list[str]:
        """Return up to 3 ranked topic candidates from the user's message.

        Uses tool-forcing to constrain Sonnet's output to structured data,
        preventing it from entering "assistant mode" with markdown/XML.
        """
        try:
            response = await asyncio.to_thread(
                tracked_messages_create,
                self.client,
                model=settings.APPETIZER_MODEL,
                max_tokens=200,
                temperature=0.0,
                system=(
                    "You are a topic extractor for the Sefaria library — a Jewish text "
                    "library covering Torah, Talmud, halacha, and all areas of Jewish "
                    "thought including universal topics like parenting, money, "
                    "relationships, health, and ethics. "
                    "Extract up to 3 topics from the user's message. "
                    "Prefer specific named topics over abstract meta-topics "
                    "(e.g. 'Shabbat' over 'Jewish Law', 'Moses' over 'Leadership'). "
                    "Each candidate should be distinct — don't return near-synonyms. "
                    "Return 'NONE' for schedule/calendar questions (current parsha, "
                    "daf yomi, today's learning) or when there is no clear topic."
                ),
                messages=[{"role": "user", "content": user_message}],
                tools=[TOPIC_EXTRACTION_TOOL],
                tool_choice={"type": "tool", "name": "extract_topics"},
            )
            tool_input = response.content[0].input
            text = tool_input.get("topics", "").strip()
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
