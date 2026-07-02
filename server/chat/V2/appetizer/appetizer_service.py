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


# Bare parsha/parasha labels that are not valid topic names on their own.
# The extractor should always emit a specific portion name (e.g. "Parashat Balak")
# when a parsha is relevant. These bare labels ground to random parshiyot and must
# be rejected at extraction time.
_BARE_PARSHA_LABELS: frozenset[str] = frozenset(
    {"parashat", "parasha", "parshah", "parshat", "parsha", "the parasha", "the parsha"}
)


def _is_bare_parsha_label(label: str) -> bool:
    """True if the label is a bare, non-specific parsha/parasha word."""
    return _normalize(label) in _BARE_PARSHA_LABELS


def _has_token_overlap(label: str, hit_text: str) -> bool:
    """True if a meaningful proportion of the label tokens appear in the hit text.

    For multi-token labels, a single incidental word match is not meaningful
    (e.g. "Lamed" from "Lamed Vav Tzaddikim"). Requires at least half the
    label tokens to overlap.
    """
    label_tokens = set(_normalize(label).split())
    hit_tokens = set(_normalize(hit_text).split())
    overlap = label_tokens & hit_tokens
    if not overlap:
        return False
    return len(overlap) * 2 >= len(label_tokens)


def _format_source_decision(returned: list[str], dropped: list[str]) -> str:
    """One human-readable line: which sources were returned and why, plus why
    any candidate was dropped. Logged to Braintrust under metadata.appetizer."""
    parts = []
    parts.append("returned: " + "; ".join(returned) if returned else "no source returned")
    if dropped:
        parts.append("dropped: " + "; ".join(dropped))
    return " | ".join(parts)


def _match_score(label: str, hit: dict) -> int:
    """Score how well a grounded hit matches the candidate label (higher = better).

    3 — exact (strong) match on title or slug: "Shabbat" → shabbat.
    2 — token overlap on title or slug: "Red Heifer" → red-heifer, "Parenting" → education,
        "la vaca roja" → red-heifer (LLM already translated to canonical English label).
    1 — the name API positioned this hit in its completion window for the query
        (any hit that reaches here is at least a fuzzy autocomplete match; used
        as a tiebreaker / minimum plausibility for transliterations like achav→ahab
        where "achav" is a curated alias for the slug "ahab").
    0 — not plausible: keep as sentinel but never accept.

    The score is used to select the best hit across the returned list and to
    gate acceptance — a score of 0 is always rejected.
    """
    if _is_strong_match(label, hit):
        return 3
    if _has_token_overlap(label, hit.get("title", "")) or _has_token_overlap(
        label, hit.get("slug", "").replace("-", " ")
    ):
        return 2
    # Any hit from the curated name-API completion window is at minimum plausible.
    # This admits transliterations (achav→ahab) without accepting truly unrelated
    # topics that appear only because the autocomplete expanded part of the query
    # (e.g. "Daf Yomi" → "Yom Kippur" via the "Yom" fragment) — those are
    # blocked by requiring the best-scoring hit to win via the caller picking the
    # highest-scoring candidate across the returned list.
    return 1


TOPIC_EXTRACTION_TOOL = {
    "name": "extract_topics",
    "description": (
        "Identify up to 3 candidate Sefaria library topics from the user's message. "
        "Use the calendar context to resolve temporal references such as "
        '"this week\'s parsha" or "today\'s daf yomi". Return an empty candidates '
        "array when the message has no extractable topic: greetings, bare citations "
        "(e.g. 'Genesis 6:13', 'yevamos 76b'), or follow-ups about prior/selected text "
        "(e.g. 'explain this', 'yes please', 'translate the selected text'). "
        "Order candidates most-central-first: the topic the user is most directly "
        "asking about comes first. Prefer fewer high-confidence candidates over more "
        "low-confidence ones."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "description": (
                    "Up to 3 topics the user is ACTIVELY asking about, ordered "
                    "most-central-first (array position is the priority signal). Include a "
                    "topic only if it is the subject the user wants sources on — never "
                    "tangential background the user only mentions in passing. Empty array "
                    "when there is no such topic."
                ),
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
                        "alternative_labels": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "1-2 broader or alternative established topic names "
                                "when the primary label is a niche concept that may not "
                                "have its own topic page. E.g. 'Lamed Vav Tzaddikim' → "
                                "['Righteous'], 'Pikuach Nefesh' → ['Saving a Life']. "
                                "Omit when the primary label is a well-known topic."
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
    "digital library of Jewish texts (Torah, Talmud, Midrash, Halacha, Kabbalah, "
    "Jewish philosophy) that organizes content under curated topic pages covering "
    "biblical figures, laws, holidays, ethical concepts, and life themes. Users come "
    "to explore primary sources, not get general information. "
    "Output is topics-only. Messages may be in any language; always return the "
    "canonical English topic name.\n"
    "</task>\n\n"
    "<relevance>\n"
    "A topic is RELEVANT only when it is the CORE SUBJECT the user wants to learn "
    "about or find sources on — removing it would leave their request unanswered. "
    "Details the user merely mentions in passing, as context, or as setup for their "
    "real question are TANGENTIAL; do not extract them.\n"
    "QUALITY OVER QUANTITY: Return only 1-2 topics that are clearly, directly relevant. "
    "A single perfect topic is better than 3 vaguely related ones. Only return a 3rd "
    "candidate when it is genuinely a distinct topic the user is asking about.\n"
    "Choose the most specific ESTABLISHED library topic that captures the user's intent "
    "— not an over-broad parent (e.g. 'Judaism' or 'Torah'), and not a hyper-narrow "
    "phrase that is not a real topic.\n"
    "</relevance>\n\n"
    "<precision_heuristic>\n"
    "A false positive (an irrelevant topic chip) is MUCH worse than a false negative "
    "(no chip). When in doubt, return fewer candidates or none.\n"
    "Return NO candidates for: greetings, test strings, bare text citations, "
    "meta-questions about the tool itself ('how does this work?', 'what can you do?'), "
    "and follow-ups that refer to prior or selected text ('explain this', 'translate "
    "that', 'yes', '?', 'go on', 'more'). "
    "Resolve temporal references using the calendar context below. "
    "Each candidate must be a SINGLE specific topic — split combined references into "
    "separate candidates. A double parsha (calendar shows e.g. 'Chukat-Balak') becomes "
    "TWO candidates 'Parashat Chukat' and 'Parashat Balak'; never emit a combined "
    "'Parashat Chukat-Balak'. "
    "PARSHA RULE: NEVER emit a bare 'Parashat', 'Parasha', 'Parsha', or 'Torah Reading' "
    "label — these are not real topics. A parsha candidate MUST be a specific portion name "
    "resolved from the calendar context (e.g. 'Parashat Pinchas'). If the user asks about "
    "the parsha but the calendar context is unavailable, return NO parsha candidates. "
    "For a daf yomi / 'today's daf' request, the tractate name itself is usually NOT a "
    "topic — instead return up to 3 of that tractate's main subject areas as concept "
    "topics, in canonical ENGLISH (not transliterated Hebrew). Use the daf_yomi tractate "
    "from the calendar context. E.g. Chullin -> 'Kashrut', 'Meat and Milk', 'Forbidden "
    "Foods'; Berakhot -> 'Prayer', 'Blessings'; Bava Kamma -> 'Damages', 'Torts'.\n"
    "BROAD THEME RULE: When the user's query is a broad everyday theme that is NOT itself "
    "a Sefaria library topic (e.g. 'parenting', 'money', 'relationships', 'health', "
    "'work', 'anger', 'grief'), emit only the 1-2 CLOSEST established Sefaria library "
    "topic names — do NOT echo the user's word as a label if it is "
    "not a real library topic. Examples: parenting -> 'Education', 'Honoring Parents'; "
    "money -> 'Money'; relationships -> 'Love'; "
    "health -> 'Medicine'; anger -> 'Anger'.\n"
    "ALTERNATIVE LABELS: When the primary label is a niche or very specific concept "
    "that may not have its own topic page (e.g. 'Lamed Vav Tzaddikim', 'Bat Kol', "
    "'Pikuach Nefesh', 'Mussar Movement'), provide 1-2 alternative_labels with the "
    "broader established topic names that cover the same area. The library organizes "
    "content under broad canonical topics, so niche concepts need a fallback. "
    "Examples: 'Lamed Vav Tzaddikim' -> ['Righteous'], 'Pikuach Nefesh' -> "
    "['Saving a Life'], 'Bat Kol' -> ['Prophecy'], 'Tikkun Olam' -> "
    "['Repairing the World']. Omit alternative_labels when the primary label is "
    "a well-known topic (e.g. 'Shabbat', 'Moses', 'Prayer').\n"
    "</precision_heuristic>\n\n"
    "<examples>\n"
    "\"what's this week's parsha?\" (calendar parsha: Chukat-Balak) -> "
    "[{label: Parashat Chukat, kind: temporal, high}, {label: Parashat Balak, kind: temporal, high}]\n"
    '"teach me about the parsha" (calendar parsha: Pinchas) -> '
    "[{label: Parashat Pinchas, kind: temporal, high}]\n"
    "\"what's today's daf yomi?\" (calendar daf_yomi: Chullin 56) -> "
    "[{label: Kashrut, kind: concept, high}, {label: Meat and Milk, kind: concept, high}, "
    "{label: Forbidden Foods, kind: concept, high}]\n"
    '"show me sources on parenting" -> [{label: Education, kind: concept, high}]\n'
    '"after my grandfather\'s funeral I want to learn about mourning" -> '
    "[{label: Mourning, kind: concept, high}]  (grandfather/funeral are tangential context)\n"
    '"help me learn about achav" -> [{label: Ahab, kind: person, high}]\n'
    '"la vaca roja" -> [{label: Red Heifer, kind: concept, high}]\n'
    '"explain this tosfos to me" -> []\n'
    '"yevamos 76 b" -> []\n'
    '"teach me about the parsha" (calendar unavailable) -> []\n'
    '"tell me about the lamed vav tzaddikim" -> '
    "[{label: Lamed Vav Tzaddikim, kind: concept, high, alternative_labels: [Righteous]}]\n"
    '"how does this work?" -> []  (meta-question about the tool)\n'
    '"tell me something interesting" -> []  (too vague for a specific topic)\n'
    "</examples>"
)


@dataclass
class Candidate:
    label: str
    kind: str
    confidence_level: str
    alternative_labels: list[str] = field(default_factory=list)


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
        metrics_sink: dict | None = None,
    ) -> AppetizerResult | None:
        """Find topic(s) for the message. When `metrics_sink` is given it is
        populated with the full metrics dict (incl. `source_decision`) even when
        the result is None, so the "why nothing was returned" reason is logged."""
        use_hebrew = interface_lang == "he"
        try:
            return await asyncio.wait_for(
                self._find_appetizer_inner(
                    user_message, use_hebrew=use_hebrew, metrics_sink=metrics_sink
                ),
                timeout=APPETIZER_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning("Appetizer timed out after %ds", APPETIZER_TIMEOUT_SECONDS)
            if metrics_sink is not None:
                metrics_sink["source_decision"] = (
                    f"no source returned — appetizer timed out after {APPETIZER_TIMEOUT_SECONDS}s"
                )
            return None
        except Exception:
            logger.exception("Appetizer failed")
            if metrics_sink is not None:
                metrics_sink["source_decision"] = "no source returned — appetizer error"
            return None

    async def _find_appetizer_inner(
        self, user_message: str, use_hebrew: bool = False, metrics_sink: dict | None = None
    ) -> AppetizerResult | None:
        metrics: dict = {"topics_found": []}
        t0 = time.monotonic()

        def _finish(result: AppetizerResult | None) -> AppetizerResult | None:
            metrics["total_ms"] = int((time.monotonic() - t0) * 1000)
            if metrics_sink is not None:
                metrics_sink.update(metrics)
            return result

        calendar_context = await self._get_calendar_context()

        t1 = time.monotonic()
        candidates = await self._extract_candidates_via_llm(user_message, calendar_context)
        metrics["llm_ms"] = int((time.monotonic() - t1) * 1000)
        metrics["llm_candidates"] = [
            {"label": c.label, "kind": c.kind, "confidence": c.confidence_level} for c in candidates
        ]

        if not candidates:
            metrics["source_decision"] = (
                "no source returned — the model extracted no topic from the message "
                "(greeting, follow-up, bare citation, or non-topic)"
            )
            return _finish(None)

        t_searches = time.monotonic()
        results = await asyncio.gather(
            *[self._ground_candidate(c, use_hebrew=use_hebrew) for c in candidates]
        )
        metrics["searches_ms"] = int((time.monotonic() - t_searches) * 1000)

        topics: list[TopicInfo] = []
        seen_slugs: set[str] = set()
        returned_notes: list[str] = []
        dropped_notes: list[str] = []
        for candidate, (topic_info, reason) in zip(candidates, results, strict=False):
            metrics.setdefault("grounding", []).append(
                {"candidate": candidate.label, "kept": topic_info is not None}
            )
            if topic_info and topic_info.topic_slug not in seen_slugs:
                seen_slugs.add(topic_info.topic_slug)
                topics.append(topic_info)
                returned_notes.append(f'"{candidate.label}" → {topic_info.topic_slug} ({reason})')
            elif topic_info:
                dropped_notes.append(
                    f'"{candidate.label}" → dropped (duplicate of {topic_info.topic_slug})'
                )
            else:
                dropped_notes.append(f'"{candidate.label}" → dropped ({reason})')

        metrics["topics_found"] = [t.topic_slug for t in topics]
        metrics["source_decision"] = _format_source_decision(returned_notes, dropped_notes)

        if not topics:
            logger.info("Appetizer: nothing grounded, metrics=%s", metrics)
            return _finish(None)

        logger.info("Appetizer: found %d topics, metrics=%s", len(topics), metrics)
        return _finish(AppetizerResult(topics=topics, metrics=metrics))

    async def _ground_candidate(
        self, candidate: Candidate, use_hebrew: bool = False
    ) -> tuple[TopicInfo | None, str]:
        """Ground one candidate against the library topic graph.

        Returns (TopicInfo, reason) when grounded, or (None, reason) when dropped;
        `reason` is a short human-readable explanation used for logging.

        Tries the primary label first. If it doesn't ground, tries each
        alternative_label (broader/related topic names the LLM provided for
        niche concepts). This lets "Lamed Vav Tzaddikim" fall back to "Righteous".
        """
        if _is_bare_parsha_label(candidate.label):
            logger.info("Appetizer: dropped bare parsha label %r", candidate.label)
            return None, "bare parsha label (no specific portion)"

        result, reason = await self._try_ground_label(
            candidate.label, candidate.confidence_level, use_hebrew
        )
        if result:
            return result, reason

        for alt in candidate.alternative_labels:
            alt_result, alt_reason = await self._try_ground_label(alt, "high", use_hebrew)
            if alt_result:
                logger.info(
                    "Appetizer: primary %r failed, alternative %r grounded",
                    candidate.label,
                    alt,
                )
                return alt_result, f"alternative '{alt}': {alt_reason}"

        return None, reason

    async def _try_ground_label(
        self, label: str, confidence_level: str, use_hebrew: bool
    ) -> tuple[TopicInfo | None, str]:
        """Try to ground a single label against the library topic pool."""
        hits = await self.sefaria_client.search_topics(label, limit=3, pool="library")
        if not hits:
            return None, "no library-pool topic found"

        if confidence_level == "high":
            best = max(hits, key=lambda h: _match_score(label, h))
            best_score = _match_score(label, best)
            if best_score < 2:
                slugs = [h.get("slug") for h in hits]
                logger.info(
                    "Appetizer: dropped high-confidence label %r — no plausible hit in %s",
                    label,
                    slugs,
                )
                return None, (
                    f"best library hit {best.get('slug')!r} scored {best_score} "
                    f"(no exact/token match) among {slugs}"
                )
            reason = "exact match" if best_score >= 3 else f"token overlap with {best['slug']!r}"
        elif not _is_strong_match(label, hits[0]):
            logger.info("Appetizer: dropped weak low-confidence label %r", label)
            return None, f"low-confidence, no exact match (top hit {hits[0].get('slug')!r})"
        else:
            best = hits[0]
            reason = "exact match (low-confidence)"
        title = best.get("he", "") if use_hebrew else best.get("title", "")
        if use_hebrew and not title:
            title = best.get("title", "")
        topic = TopicInfo(
            topic_slug=best["slug"],
            topic_title=title,
            topic_url=f"{SEFARIA_TOPICS_BASE_URL}/{best['slug']}",
        )
        return topic, reason

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
                if _is_bare_parsha_label(label):
                    logger.info(
                        "Appetizer: extractor emitted bare parsha label %r — dropped", label
                    )
                    continue
                alt_labels = [
                    alt.strip()
                    for alt in (c.get("alternative_labels") or [])
                    if alt and alt.strip()
                ]
                candidates.append(
                    Candidate(
                        label=label,
                        kind=c.get("kind", "concept"),
                        confidence_level=c.get("confidence_level", "low"),
                        alternative_labels=alt_labels,
                    )
                )
            logger.info("Appetizer extracted %r for prompt: %r", candidates, user_message)
            return candidates[:3]
        except Exception:
            logger.exception("LLM candidate extraction failed")
            return []


get_appetizer_service, reset_appetizer_service = make_singleton(AppetizerService)
