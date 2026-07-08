# General Grounded Topic-Finding Appetizer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Replace the overfit parsha/daf-yomi intent gates in the appetizer with one general pipeline: calendar context → single structured LLM extraction → grounding-confidence gate.

**Architecture:** Always inject a daily-cached, compact calendar context block so temporal queries ("this week's parsha", "today's daf yomi") resolve generally inside one LLM call that emits candidates with `kind` + `confidence_level`. A code-side grounding gate keeps only candidates that match a real `pool="library"` topic, with low-confidence candidates required to match exactly. "Return nothing" becomes a general outcome, not enumerated suppression.

**Tech Stack:** Python 3, Django, `anthropic` tool-forcing, `httpx`, `pytest`/`pytest-asyncio`.

## Global Constraints

- User-visible appetizer response under **5 seconds**; `APPETIZER_TIMEOUT_SECONDS <= 5`; fail closed (return `None`) on timeout or any exception.
- Topics-only output. The service receives **only the single user message** — no conversation history, no selected text.
- Conventional Commits (`feat:`, `refactor:`, `test:`). Test before committing.
- Run backend tests with: `cd server && DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/V2/appetizer/test_appetizer_service.py -v`
- Keep `search_topics()` return shape unchanged (`[{title, slug}]`, plus `he` when `pool` is set). No new live endpoints in the request path.

---

### Task 1: Calendar context renderer (pure function)

**Files:**
- Create: `server/chat/V2/appetizer/calendar_context.py`
- Test: `server/chat/V2/appetizer/test_calendar_context.py`

**Interfaces:**
- Produces: `render_calendar_context(calendar: dict) -> str` — returns an XML-tagged block of `field: value` lines, or `"<calendar_context>unavailable</calendar_context>"` when no known items are present.

- [x] **Step 1: Write the failing test**

```python
# server/chat/V2/appetizer/test_calendar_context.py
from .calendar_context import render_calendar_context


def test_render_includes_known_learning_schedules():
    calendar = {
        "Gregorian Date": "2026-06-25T09:00:00",
        "calendar_items": [
            {"title": {"en": "Parashat Hashavua"}, "displayValue": {"en": "Chukat-Balak"}},
            {"title": {"en": "Haftarah"}, "displayValue": {"en": "Judges 11:1-33"}},
            {"title": {"en": "Daf Yomi"}, "displayValue": {"en": "Chullin 56"}},
            {"title": {"en": "Mishnah Yomi"}, "displayValue": {"en": "Keilim 3:5-6"}},
            {"title": {"en": "Unknown Cycle"}, "displayValue": {"en": "ignore me"}},
        ],
    }
    result = render_calendar_context(calendar)
    assert result.startswith("<calendar_context>")
    assert result.endswith("</calendar_context>")
    assert "date: 2026-06-25" in result
    assert "parsha: Chukat-Balak" in result
    assert "haftarah: Judges 11:1-33" in result
    assert "daf_yomi: Chullin 56" in result
    assert "mishnah_yomi: Keilim 3:5-6" in result
    assert "ignore me" not in result  # unknown titles dropped


def test_render_unavailable_when_no_known_items():
    assert render_calendar_context({"calendar_items": []}) == (
        "<calendar_context>unavailable</calendar_context>"
    )
    assert render_calendar_context({}) == (
        "<calendar_context>unavailable</calendar_context>"
    )
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd server && DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/V2/appetizer/test_calendar_context.py -v`
Expected: FAIL with `ModuleNotFoundError`/`ImportError` for `calendar_context`.

- [x] **Step 3: Write minimal implementation**

```python
# server/chat/V2/appetizer/calendar_context.py
"""Render Sefaria's calendar API payload into a compact, high-signal context block.

Kept minimal on purpose (context rot): only learning-schedule cycles that real
traffic asks for. Add fields only on observed failures.
"""

from __future__ import annotations

# Sefaria calendar item en-title -> compact field name. One canonical field per concept.
_CALENDAR_FIELDS: dict[str, str] = {
    "Parashat Hashavua": "parsha",
    "Haftarah": "haftarah",
    "Daf Yomi": "daf_yomi",
    "Mishnah Yomi": "mishnah_yomi",
    "Daily Rambam": "rambam_yomi",
    "Daily Rambam (3 Chapters)": "rambam_yomi",
    "Yerushalmi Yomi": "yerushalmi_yomi",
    "Tanakh Yomi": "tanakh_yomi",
}


def render_calendar_context(calendar: dict) -> str:
    lines: list[str] = []
    date = (calendar.get("Gregorian Date") or "")[:10]
    if date:
        lines.append(f"date: {date}")

    seen: set[str] = set()
    for item in calendar.get("calendar_items", []) or []:
        en_title = (item.get("title") or {}).get("en", "")
        field = _CALENDAR_FIELDS.get(en_title)
        if not field or field in seen:
            continue
        value = (item.get("displayValue") or {}).get("en", "").strip()
        if not value:
            continue
        seen.add(field)
        lines.append(f"{field}: {value}")

    if len(lines) <= 1 and "date" in (lines[0] if lines else ""):
        # only a date, no schedules → treat as unavailable for the model's purposes
        if not seen:
            return "<calendar_context>unavailable</calendar_context>"
    if not lines:
        return "<calendar_context>unavailable</calendar_context>"

    body = "\n".join(lines)
    return f"<calendar_context>\n{body}\n</calendar_context>"
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd server && DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/V2/appetizer/test_calendar_context.py -v`
Expected: PASS (2 passed).

- [x] **Step 5: Commit**

```bash
git add server/chat/V2/appetizer/calendar_context.py server/chat/V2/appetizer/test_calendar_context.py
git commit -m "feat(appetizer): render calendar context block for topic extraction"
```

---

### Task 2: Daily-cached calendar context on AppetizerService

**Files:**
- Modify: `server/chat/V2/appetizer/appetizer_service.py` (imports, `__init__`, new `_get_calendar_context`)
- Test: `server/chat/V2/appetizer/test_appetizer_service.py` (append)

**Interfaces:**
- Consumes: `render_calendar_context` (Task 1), `SefariaClient.get_current_calendar()`.
- Produces: `AppetizerService._get_calendar_context() -> str` — daily-cached; never raises (returns the `unavailable` block on error).

- [x] **Step 1: Write the failing test**

```python
# Append to test_appetizer_service.py
@pytest.mark.asyncio
async def test_calendar_context_cached_per_day():
    service = AppetizerService.__new__(AppetizerService)
    service._calendar_cache = None
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {
        "Gregorian Date": "2026-06-25T09:00:00",
        "calendar_items": [
            {"title": {"en": "Daf Yomi"}, "displayValue": {"en": "Chullin 56"}},
        ],
    }
    first = await service._get_calendar_context()
    second = await service._get_calendar_context()
    assert "daf_yomi: Chullin 56" in first
    assert first == second
    service.sefaria_client.get_current_calendar.assert_called_once()  # cached


@pytest.mark.asyncio
async def test_calendar_context_unavailable_on_error():
    service = AppetizerService.__new__(AppetizerService)
    service._calendar_cache = None
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.side_effect = Exception("boom")
    result = await service._get_calendar_context()
    assert result == "<calendar_context>unavailable</calendar_context>"
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd server && DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/V2/appetizer/test_appetizer_service.py::test_calendar_context_cached_per_day -v`
Expected: FAIL with `AttributeError: ... '_get_calendar_context'`.

- [x] **Step 3: Write minimal implementation**

In `appetizer_service.py`, add to the imports near the top:

```python
from datetime import datetime

from .calendar_context import render_calendar_context
```

Add the cache field to `AppetizerService.__init__` (currently sets `self.client` and `self.sefaria_client`):

```python
    def __init__(self):
        self.client = get_anthropic_client()
        self.sefaria_client = SefariaClient()
        self._calendar_cache: tuple[str, str] | None = None
```

Add the method to `AppetizerService`:

```python
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
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd server && DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/V2/appetizer/test_appetizer_service.py::test_calendar_context_cached_per_day chat/V2/appetizer/test_appetizer_service.py::test_calendar_context_unavailable_on_error -v`
Expected: PASS (2 passed).

- [x] **Step 5: Commit**

```bash
git add server/chat/V2/appetizer/appetizer_service.py server/chat/V2/appetizer/test_appetizer_service.py
git commit -m "feat(appetizer): daily-cached calendar context on the service"
```

---

### Task 3: Structured extraction (Candidate + new tool schema)

**Files:**
- Modify: `server/chat/V2/appetizer/appetizer_service.py` (add `Candidate`, replace `TOPIC_EXTRACTION_TOOL`, rewrite `_extract_candidates_via_llm`)
- Test: `server/chat/V2/appetizer/test_appetizer_service.py` (append)

**Interfaces:**
- Consumes: `tracked_messages_create`, `settings.APPETIZER_MODEL`, a calendar context string.
- Produces:
  - `@dataclass Candidate` with `label: str`, `kind: str`, `confidence_level: str`.
  - `AppetizerService._extract_candidates_via_llm(user_message: str, calendar_context: str) -> list[Candidate]` (≤ 3; empty list = NONE).

- [x] **Step 1: Write the failing test**

```python
# Append to test_appetizer_service.py
from ..appetizer.appetizer_service import Candidate


def _fake_tool_response(candidates):
    resp = MagicMock()
    block = MagicMock()
    block.input = {"candidates": candidates}
    resp.content = [block]
    return resp


@pytest.mark.asyncio
async def test_extract_parses_candidates():
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    with patch(
        "chat.V2.appetizer.appetizer_service.tracked_messages_create",
        return_value=_fake_tool_response(
            [
                {"label": "Parenting", "kind": "concept", "confidence_level": "high"},
                {"label": "", "kind": "concept", "confidence_level": "low"},  # dropped: empty label
            ]
        ),
    ):
        result = await service._extract_candidates_via_llm("sources on parenting", "<calendar_context>unavailable</calendar_context>")
    assert result == [Candidate(label="Parenting", kind="concept", confidence_level="high")]


@pytest.mark.asyncio
async def test_extract_empty_is_none():
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    with patch(
        "chat.V2.appetizer.appetizer_service.tracked_messages_create",
        return_value=_fake_tool_response([]),
    ):
        result = await service._extract_candidates_via_llm("yes please", "<calendar_context>unavailable</calendar_context>")
    assert result == []


@pytest.mark.asyncio
async def test_extract_returns_empty_on_exception():
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    with patch(
        "chat.V2.appetizer.appetizer_service.tracked_messages_create",
        side_effect=Exception("api down"),
    ):
        result = await service._extract_candidates_via_llm("anything", "<calendar_context>unavailable</calendar_context>")
    assert result == []
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd server && DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/V2/appetizer/test_appetizer_service.py::test_extract_parses_candidates -v`
Expected: FAIL with `ImportError: cannot import name 'Candidate'`.

- [x] **Step 3: Write minimal implementation**

In `appetizer_service.py`, add the dataclass next to `TopicInfo`:

```python
@dataclass
class Candidate:
    label: str
    kind: str
    confidence_level: str
```

Replace the entire `TOPIC_EXTRACTION_TOOL` constant with:

```python
TOPIC_EXTRACTION_TOOL = {
    "name": "extract_topics",
    "description": (
        "Identify up to 3 candidate Sefaria library topics from the user's message. "
        "Use the calendar context to resolve temporal references such as "
        "\"this week's parsha\" or \"today's daf yomi\". Return an empty candidates "
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
    "references using the calendar context below.\n"
    "</precision_heuristic>\n\n"
    "<examples>\n"
    "\"what's today's daf yomi?\" -> [{label: <daf_yomi tractate>, kind: temporal, high}]\n"
    "\"show me sources on parenting\" -> [{label: Parenting, kind: concept, high}]\n"
    "\"help me learn about achav\" -> [{label: Ahab, kind: person, high}]\n"
    "\"la vaca roja\" -> [{label: Red Heifer, kind: concept, high}]\n"
    "\"explain this tosfos to me\" -> []\n"
    "\"yevamos 76 b\" -> []\n"
    "</examples>"
)
```

Rewrite `_extract_candidates_via_llm`:

```python
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
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd server && DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/V2/appetizer/test_appetizer_service.py::test_extract_parses_candidates chat/V2/appetizer/test_appetizer_service.py::test_extract_empty_is_none chat/V2/appetizer/test_appetizer_service.py::test_extract_returns_empty_on_exception -v`
Expected: PASS (3 passed). The broader suite is still red here (old flow/intent tests) — fixed in Task 4.

- [x] **Step 5: Commit**

```bash
git add server/chat/V2/appetizer/appetizer_service.py server/chat/V2/appetizer/test_appetizer_service.py
git commit -m "feat(appetizer): structured topic extraction with kind and confidence"
```

---

### Task 4: Grounding gate + pipeline rewrite (delete intent gates)

**Files:**
- Modify: `server/chat/V2/appetizer/appetizer_service.py` (delete `_PARSHA_INTENT_RE`, `_DAF_YOMI_SUPPRESS_RE`, `_GENERIC_BLOCKLIST`, `_handle_parsha_intent`, `_search_and_build`; rewrite `_find_appetizer_inner`; add `_normalize`, `_is_strong_match`, `_ground_candidate`)
- Test: `server/chat/V2/appetizer/test_appetizer_service.py` (delete obsolete tests; update flow tests)

**Interfaces:**
- Consumes: `Candidate`, `_get_calendar_context`, `_extract_candidates_via_llm` (Tasks 2–3), `search_topics(query, limit, pool)`.
- Produces:
  - `_is_strong_match(label: str, hit: dict) -> bool` (module-level).
  - `AppetizerService._ground_candidate(candidate: Candidate, use_hebrew: bool) -> TopicInfo | None`.
  - Rewritten `_find_appetizer_inner` returning `AppetizerResult | None`.

- [x] **Step 1: Delete obsolete regex/blocklist tests and update flow-test mocks**

In `test_appetizer_service.py`, **delete these whole test functions** (they assert deleted behavior): `test_daf_yomi_intent_returns_none`, `test_daf_yomi_phrase_returns_none`, `test_recent_dapim_returns_none`, `test_current_parsha_uses_calendar_not_llm`, `test_current_parsha_double_portion_returns_two_topics`, `test_current_parsha_returns_none_when_calendar_missing`, `test_parshat_hashavua_phrase_uses_calendar`, `test_generic_candidates_suppressed_by_blocklist`, `test_all_generic_candidates_returns_none`.

In every remaining `AppetizerService` flow test that mocks `_extract_candidates_via_llm` (these currently return lists of **strings**), make two changes so they fit the new pipeline:

1. Add this line after `service.sefaria_client = AsyncMock()`:
   `service.sefaria_client.get_current_calendar.return_value = {}`
2. Change the `mock_llm.return_value = [...]` strings into `Candidate` objects with `confidence_level="high"`. For example, `test_returns_multiple_topics` becomes:

```python
    mock_llm.return_value = [
        Candidate("Shabbat", "concept", "high"),
        Candidate("Kiddush", "concept", "high"),
        Candidate("Havdalah", "concept", "high"),
    ]
```

Apply the same shape to: `test_partial_topics_on_mixed_hits` (`["Shabbat","Nonexistent","Havdalah"]`), `test_deduplicates_topics_by_slug` (`["Shabbat","Sabbath"]`), `test_first_candidate_hits` (`["Shabbat","Sabbath"]`), `test_fallback_to_second_candidate` (`["Herod the Great","Herod"]`), `test_hebrew_prompt` (`["Sivan"]`), `test_returns_none_when_all_candidates_miss` (`["candidate1","candidate2"]`), `test_returns_none_when_llm_returns_empty` (`[]`), `test_hebrew_interface_lang_returns_hebrew_title` (`["Shabbat"]`), `test_english_interface_lang_returns_english_title` (`["Shabbat"]`), `test_appetizer_passes_pool_library_to_search_topics` (`["Shabbat"]`).

For `test_appetizer_passes_pool_library_to_search_topics`, the asserted call uses the candidate label, which now must match exactly for a low-confidence path — keep `confidence_level="high"` so the gate accepts regardless. The assertion line stays:
`service.sefaria_client.search_topics.assert_called_once_with("Shabbat", limit=3, pool="library")`.

- [x] **Step 2: Run the suite to verify the expected failures**

Run: `cd server && DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/V2/appetizer/test_appetizer_service.py -v`
Expected: FAIL — flow tests error because `_find_appetizer_inner` still references deleted helpers / old string flow. (This confirms the tests now drive the rewrite.)

- [x] **Step 3: Rewrite the service pipeline**

In `appetizer_service.py`, **delete** these module-level constants and methods entirely: `_PARSHA_INTENT_RE`, `_DAF_YOMI_SUPPRESS_RE`, `_GENERIC_BLOCKLIST`, the method `_handle_parsha_intent`, and the method `_search_and_build`. Keep `import re` (used by `_normalize`).

Add module-level helpers (near the top, after the constants):

```python
def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _is_strong_match(label: str, hit: dict) -> bool:
    """True when the candidate label matches the grounded topic's title or slug exactly
    (normalized). Used to admit low-confidence candidates only on an exact hit."""
    label_n = _normalize(label)
    return label_n == _normalize(hit.get("title", "")) or label_n == _normalize(hit.get("slug", ""))
```

Replace `_find_appetizer_inner` with:

```python
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
            {"label": c.label, "kind": c.kind, "confidence": c.confidence_level}
            for c in candidates
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
```

- [x] **Step 4: Run the suite to verify it passes**

Run: `cd server && DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/V2/appetizer/test_appetizer_service.py -v`
Expected: PASS (all remaining flow + `search_topics` + extraction + calendar tests green).

- [x] **Step 5: Commit**

```bash
git add server/chat/V2/appetizer/appetizer_service.py server/chat/V2/appetizer/test_appetizer_service.py
git commit -m "refactor(appetizer): general grounding gate, remove parsha/daf-yomi intent gates"
```

---

### Task 5: Taxonomy regression tests + grounding-gate unit tests

**Files:**
- Test: `server/chat/V2/appetizer/test_appetizer_service.py` (append)

**Interfaces:**
- Consumes: `Candidate`, `_is_strong_match`, the rewritten pipeline (Task 4).

- [x] **Step 1: Write the failing tests**

```python
# Append to test_appetizer_service.py
from ..appetizer.appetizer_service import _is_strong_match


def test_is_strong_match_normalizes_title_and_slug():
    assert _is_strong_match("Ahab", {"title": "Ahab", "slug": "ahab"})
    assert _is_strong_match("Red Heifer", {"title": "Red Heifer", "slug": "red-heifer"})
    assert _is_strong_match("parashat balak", {"title": "Parashat Balak", "slug": "parashat-balak"})
    assert not _is_strong_match("number six", {"title": "Genesis", "slug": "genesis"})


@pytest.mark.asyncio
async def test_low_confidence_weak_match_dropped():
    """Low-confidence candidate that only fuzzy-grounds is dropped."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    # search returns a topic that does NOT exactly match the vague label
    service.sefaria_client.search_topics.return_value = [
        {"title": "Genesis", "slug": "genesis", "he": "בראשית"}
    ]
    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [Candidate("the number six", "concept", "low")]
        result = await service.find_appetizer("is the number six special?")
    assert result is None


@pytest.mark.asyncio
async def test_low_confidence_exact_match_kept():
    """Low-confidence candidate that grounds exactly is kept."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    service.sefaria_client.search_topics.return_value = [
        {"title": "Shofar", "slug": "shofar", "he": "שופר"}
    ]
    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [Candidate("Shofar", "concept", "low")]
        result = await service.find_appetizer("shofar")
    assert result is not None
    assert result.topics[0].topic_slug == "shofar"


@pytest.mark.asyncio
async def test_none_taxonomy_cases_return_none():
    """Greetings / follow-ups / bare citations: LLM yields no candidates -> None."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    for msg in ["<AUTO TEST> Say hi", "explain this to me", "yevamos 76 b", "yes please"]:
        with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = []
            result = await service.find_appetizer(msg)
        assert result is None, msg
    service.sefaria_client.search_topics.assert_not_called()


@pytest.mark.asyncio
async def test_temporal_candidate_grounds_to_tractate():
    """Daf-yomi style query resolves (in extraction) to a tractate that grounds."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.get_current_calendar.return_value = {}
    service.sefaria_client.search_topics.return_value = [
        {"title": "Chullin", "slug": "chullin", "he": "חולין"}
    ]
    with patch.object(service, "_extract_candidates_via_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = [Candidate("Chullin", "temporal", "high")]
        result = await service.find_appetizer("what's today's daf yomi?")
    assert result is not None
    assert result.topics[0].topic_slug == "chullin"
```

- [x] **Step 2: Run to verify they pass against the Task 4 implementation**

Run: `cd server && DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/V2/appetizer/test_appetizer_service.py -v`
Expected: PASS (full file green).

- [x] **Step 3: Run the whole backend appetizer + client suite**

Run: `cd server && DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/V2/appetizer/ -v`
Expected: PASS (all appetizer tests).

- [x] **Step 4: Commit**

```bash
git add server/chat/V2/appetizer/test_appetizer_service.py
git commit -m "test(appetizer): taxonomy regressions and grounding-gate unit tests"
```

---

### Task 6: Update the appetizer module docstring + plan/spec sync

**Files:**
- Modify: `server/chat/V2/appetizer/appetizer_service.py` (module docstring at top)
- Modify: `docs/superpowers/plans/2026-06-25-appetizer-general-topic-finding.md` (mark tasks complete as you go)

**Interfaces:** none.

- [x] **Step 1: Replace the module docstring**

The current docstring describes the deleted intent-gate pipeline. Replace the top-of-file docstring with:

```python
"""Topic appetizer — finds relevant Sefaria topics within 5 seconds.

Pipeline:
1. Build a daily-cached calendar context block (date + learning schedules).
2. One structured LLM call extracts up to 3 candidates (label + kind + confidence).
3. Each candidate is grounded against the library topic pool; low-confidence
   candidates are kept only on an exact match. No grounded topic -> return None.

The outer asyncio.wait_for hard-cap is 5 seconds.
"""
```

- [x] **Step 2: Run the full appetizer suite once more**

Run: `cd server && DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/V2/appetizer/ -v`
Expected: PASS.

- [x] **Step 3: Commit**

```bash
git add server/chat/V2/appetizer/appetizer_service.py docs/superpowers/plans/2026-06-25-appetizer-general-topic-finding.md
git commit -m "docs(appetizer): update module docstring for general pipeline"
```

---

## Self-Review

**Spec coverage:**
- Calendar context (daily-cached, XML, minimal fields) → Tasks 1–2. ✓
- Structured extraction (Candidate, enums, NONE=empty, multilingual, few-shot) → Task 3. ✓
- Grounding gate (exact/primary accept; low-confidence requires exact; no match drop; dedup) → Task 4 (`_ground_candidate`, `_is_strong_match`). ✓
- Delete `_PARSHA_INTENT_RE` / `_DAF_YOMI_SUPPRESS_RE` / `_GENERIC_BLOCKLIST` / `_handle_parsha_intent` → Task 4. ✓
- Rewrite suppression tests → Task 4 Step 1 (deletions) + Task 5 (new taxonomy). ✓
- Metrics (general reason codes) → Task 4 `_find_appetizer_inner` metrics dict. ✓
- 5s budget test, `search_topics` ranking test → already present (`test_appetizer_timeout_is_at_most_5_seconds`, `test_search_topics_primary_preferred_over_alias`), retained. ✓

**Note on the spec's "is_primary → accept" gate:** `search_topics(pool="library")` already sorts `is_primary` first and returns the canonical title, so `_ground_candidate` trusts `hits[0]` as the primary/best match; `_is_strong_match` approximates the "exact match" admit-rule for low-confidence candidates. No change to `search_topics` is required, keeping its return shape compatible.

**Placeholder scan:** none — every step has concrete code/commands.

**Type consistency:** `Candidate(label, kind, confidence_level)` used identically in Tasks 3–5; `_ground_candidate` / `_is_strong_match` / `_get_calendar_context` / `render_calendar_context` signatures match across consume/produce blocks.
