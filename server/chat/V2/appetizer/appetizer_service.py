"""Topic appetizer — finds relevant Sefaria topics within 5 seconds.

Pipeline:
1. Build a daily-cached calendar context block (date + learning schedules).
2. One structured LLM call extracts up to 3 candidates (label + kind + confidence).
3. Each candidate is grounded against the library topic pool; low-confidence
   candidates are kept only on an exact match. No grounded topic -> return None.

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


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _is_strong_match(label: str, hit: dict) -> bool:
    """True when the candidate label matches the grounded topic's title or slug exactly
    (normalized). Used to admit low-confidence candidates only on an exact hit."""
    label_n = _normalize(label)
    return label_n == _normalize(hit.get("title", "")) or label_n == _normalize(hit.get("slug", ""))


TOPIC_EXTRACTION_TOOL = {
    "name": "extract_topics",
    "description": (
        "Identify up to 3 candidate Sefaria library topics from the user's message. "
        "Use the calendar context to resolve temporal references such as "
        '"this week\'s parsha" or "today\'s daf yomi". Return an empty candidates '
        "array when the message has no extractable topic: greetings, bare citations "
        "(e.g. 'Genesis 6:13', 'yevamos 76b'), or follow-ups about prior/selected text "
        "(e.g. 'explain this', 'yes please', 'translate the selected text'). "
        "Prefer fewer high-confidence candidates over more low-confidence ones."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {
                            "type": "string",
                            "description": (
                                "Canonical English topic name, shortest form "
                                "(e.g. 'Moses' not 'Moses our Teacher', 'Parashat Balak' "
                                "for a parsha, 'Chullin' for a tractate). Translate "
                                "non-English messages to the canonical English name."
                            ),
                        },
                        "kind": {
                            "type": "string",
                            "enum": ["person", "place", "concept", "temporal"],
                            "description": (
                                "person=named individual; place=geographic; "
                                "concept=theme/law/idea; temporal=resolved from calendar."
                            ),
                        },
                        "confidence_level": {
                            "type": "string",
                            "enum": ["high", "low"],
                            "description": (
                                "high = a clear, specific topic the library plausibly has. "
                                "low = plausible but the message is vague or underspecified."
                            ),
                        },
                    },
                    "required": ["label", "kind", "confidence_level"],
                },
            }
        },
        "required": ["candidates"],
    },
}

EXTRACTION_SYSTEM_PROMPT = (
    "<task>\n"
    "You extract candidate topics from one user message for the Sefaria library — a "
    "Jewish text library (Torah, Talmud, halacha, Jewish thought) that also covers "
    "universal themes like parenting, money, relationships, health, and ethics. "
    "Output is topics-only. Messages may be in any language; always return the "
    "canonical English topic name.\n"
    "</task>\n\n"
    "<precision_heuristic>\n"
    "Prefer returning fewer high-confidence candidates, or none. A false positive "
    "(a chip on a non-topic) is worse than a false negative. Return NO candidates for "
    "greetings, test strings, bare text citations, and follow-ups that refer to prior "
    "or selected text ('explain this', 'translate that', 'yes', '?'). Resolve temporal "
    "references using the calendar context below. "
    "Each candidate must be a SINGLE specific topic — split combined references into "
    "separate candidates. A double parsha (calendar shows e.g. 'Chukat-Balak') becomes "
    "TWO candidates 'Parashat Chukat' and 'Parashat Balak'; never emit a combined "
    "'Parashat Chukat-Balak'. For a daf yomi reference give the tractate name only "
    "(e.g. 'Chullin'), not the daf number.\n"
    "</precision_heuristic>\n\n"
    "<examples>\n"
    "\"what's this week's parsha?\" (calendar parsha: Chukat-Balak) -> "
    "[{label: Parashat Chukat, kind: temporal, high}, {label: Parashat Balak, kind: temporal, high}]\n"
    "\"what's today's daf yomi?\" -> [{label: <daf_yomi tractate, e.g. Chullin>, kind: temporal, high}]\n"
    '"show me sources on parenting" -> [{label: Parenting, kind: concept, high}]\n'
    '"help me learn about achav" -> [{label: Ahab, kind: person, high}]\n'
    '"la vaca roja" -> [{label: Red Heifer, kind: concept, high}]\n'
    '"explain this tosfos to me" -> []\n'
    '"yevamos 76 b" -> []\n'
    "</examples>"
)


@dataclass
class Candidate:
    label: str
    kind: str
    confidence_level: str


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
        """Compact calendar block, fetched at most once per day per process.

        Only successful fetches are cached; a transient failure returns the
        unavailable block without caching, so the next request retries.
        """
        today = datetime.now().date().isoformat()
        cache = getattr(self, "_calendar_cache", None)
        if cache and cache[0] == today:
            return cache[1]
        try:
            calendar = await self.sefaria_client.get_current_calendar()
            rendered = render_calendar_context(calendar)
        except Exception:
            logger.exception("Appetizer: calendar fetch failed")
            return "<calendar_context>unavailable</calendar_context>"
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

        calendar_context = await self._get_calendar_context()

        t1 = time.monotonic()
        candidates = await self._extract_candidates_via_llm(user_message, calendar_context)
        metrics["llm_ms"] = int((time.monotonic() - t1) * 1000)
        metrics["llm_candidates"] = [
            {"label": c.label, "kind": c.kind, "confidence": c.confidence_level} for c in candidates
        ]

        if not candidates:
            metrics["total_ms"] = int((time.monotonic() - t0) * 1000)
            return None

        t_searches = time.monotonic()
        results = await asyncio.gather(
            *[self._ground_candidate(c, use_hebrew=use_hebrew) for c in candidates]
        )
        metrics["searches_ms"] = int((time.monotonic() - t_searches) * 1000)

        topics: list[TopicInfo] = []
        seen_slugs: set[str] = set()
        for candidate, topic_info in zip(candidates, results, strict=False):
            metrics.setdefault("grounding", []).append(
                {"candidate": candidate.label, "kept": topic_info is not None}
            )
            if topic_info and topic_info.topic_slug not in seen_slugs:
                seen_slugs.add(topic_info.topic_slug)
                topics.append(topic_info)

        metrics["topics_found"] = [t.topic_slug for t in topics]
        metrics["total_ms"] = int((time.monotonic() - t0) * 1000)

        if not topics:
            logger.info("Appetizer: nothing grounded, metrics=%s", metrics)
            return None

        logger.info("Appetizer: found %d topics, metrics=%s", len(topics), metrics)
        return AppetizerResult(topics=topics, metrics=metrics)

    async def _ground_candidate(
        self, candidate: Candidate, use_hebrew: bool = False
    ) -> TopicInfo | None:
        """Ground one candidate against the library topic graph. Low-confidence
        candidates are kept only on an exact (strong) match."""
        hits = await self.sefaria_client.search_topics(candidate.label, limit=3, pool="library")
        if not hits:
            return None
        best = hits[0]
        if candidate.confidence_level != "high" and not _is_strong_match(candidate.label, best):
            logger.info("Appetizer: dropped weak low-confidence candidate %r", candidate.label)
            return None
        title = best.get("he", "") if use_hebrew else best.get("title", "")
        return TopicInfo(
            topic_slug=best["slug"],
            topic_title=title,
            topic_url=f"{SEFARIA_TOPICS_BASE_URL}/{best['slug']}",
        )

    async def _extract_candidates_via_llm(
        self, user_message: str, calendar_context: str
    ) -> list[Candidate]:
        """Return up to 3 structured candidates from the user's message (empty = NONE)."""
        try:
            response = await asyncio.to_thread(
                tracked_messages_create,
                self.client,
                model=settings.APPETIZER_MODEL,
                max_tokens=300,
                temperature=0.0,
                system=f"{EXTRACTION_SYSTEM_PROMPT}\n\n{calendar_context}",
                messages=[{"role": "user", "content": user_message}],
                tools=[TOPIC_EXTRACTION_TOOL],
                tool_choice={"type": "tool", "name": "extract_topics"},
            )
            raw = response.content[0].input.get("candidates", []) or []
            candidates: list[Candidate] = []
            for c in raw:
                label = (c.get("label") or "").strip()
                if not label:
                    continue
                candidates.append(
                    Candidate(
                        label=label,
                        kind=c.get("kind", "concept"),
                        confidence_level=c.get("confidence_level", "low"),
                    )
                )
            logger.info("Appetizer extracted %r for prompt: %r", candidates, user_message)
            return candidates[:3]
        except Exception:
            logger.exception("LLM candidate extraction failed")
            return []


get_appetizer_service, reset_appetizer_service = make_singleton(AppetizerService)
