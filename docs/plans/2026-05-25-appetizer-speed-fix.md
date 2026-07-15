# Appetizer Speed Fix — Hit the 5-Second SLA

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the topic appetizer appear within 5 seconds using a two-tier approach: fast direct API search first (<500ms), Haiku fallback only when needed (<5s total).

**Architecture:** Tier 1 strips common prompt wrappers via regex and sends the topical core directly to Sefaria's `api/name/{query}` endpoint, which does fuzzy autocomplete and returns topic matches in ~200-500ms. If Tier 1 returns zero topics (abstract/conceptual prompts like "if a person mixes Torah thinking with outside wisdom..."), Tier 2 kicks in: a Haiku LLM call extracts the concept (~2-4s) and retries the API search. Both tiers run inside a single 5-second `asyncio.wait_for` timeout. Most prompts hit Tier 1 and resolve in <1s. Abstract prompts hit Tier 2 and resolve in <5s.

**Tech Stack:** Python (Django), httpx, Anthropic Claude Haiku, Sefaria REST API

---

## Two-Tier Flow

```
User prompt
  │
  ├─ Tier 1: strip prefixes → api/name/{keywords} (<500ms)
  │    ├─ Got topics? → DONE ✅ (<1s)
  │    └─ No topics? ↓
  │
  └─ Tier 2: Haiku extracts concept → api/name/{concept} (2-4s)
       ├─ Got topics? → DONE ✅ (<5s)
       └─ No topics? → No appetizer (silent)
```

## Why Two Tiers

- **Most prompts have a recognizable topic name** ("find sources about Shabbat", "what is the Rambam's view on prayer"). Tier 1 handles these in <500ms — no LLM needed, no cost.
- **Some prompts are abstract/conceptual** ("if a person mixes Torah thinking with outside wisdom..."). No keyword maps to a topic. Haiku can extract "Torah and secular wisdom" and the API matches the topic. This is the edge case that justifies keeping Haiku as a fallback.
- **5-second timeout wraps both tiers.** If Tier 1 hits, Haiku never runs. If Tier 1 misses, there's ~4.5s budget left for Haiku.

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `server/chat/V2/appetizer/appetizer_service.py` | Two-tier architecture: direct keywords first, Haiku fallback |
| Modify | `server/chat/V2/agent/sefaria_client.py:307-318` | Restore `limit` param (without `type` filter) |
| Modify | `server/chat/V2/appetizer/test_appetizer_service.py` | Tests for both tiers |

---

### Task 1: Implement Two-Tier AppetizerService

**Files:**
- Modify: `server/chat/V2/appetizer/appetizer_service.py`

- [ ] **Step 1: Write tests for keyword extraction**

Add to `server/chat/V2/appetizer/test_appetizer_service.py`:

```python
from ..appetizer.appetizer_service import _extract_query_words


def test_extract_query_words_strips_common_prefixes():
    assert _extract_query_words("find me sources about Shabbat") == "Shabbat"
    assert _extract_query_words("tell me about the divine attributes") == "divine attributes"
    assert _extract_query_words("what does the Torah say about time") == "time"


def test_extract_query_words_strips_please_variants():
    assert _extract_query_words("please find sources about Rambam") == "Rambam"
    assert _extract_query_words("can you show me texts on prayer") == "prayer"


def test_extract_query_words_preserves_short_queries():
    assert _extract_query_words("Shabbat") == "Shabbat"
    assert _extract_query_words("Rambam") == "Rambam"


def test_extract_query_words_handles_hebrew():
    assert _extract_query_words("מה אומרת התורה על שבת") == "מה אומרת התורה על שבת"


def test_extract_query_words_strips_trailing_punctuation():
    assert _extract_query_words("what is Shabbat?") == "Shabbat"


def test_extract_query_words_empty():
    assert _extract_query_words("") == ""
    assert _extract_query_words("   ") == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/yotamfromm/dev/sefaria/ai-chatbot && server/venv/bin/python -m pytest server/chat/V2/appetizer/test_appetizer_service.py::test_extract_query_words_strips_common_prefixes -v`
Expected: FAIL — `_extract_query_words` not defined.

- [ ] **Step 3: Implement the full two-tier service**

Replace `server/chat/V2/appetizer/appetizer_service.py`:

```python
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
        if query:
            result = await self._search_and_build(query)
            if result:
                logger.info("Appetizer tier-1 hit for query=%r", query)
                return result

        # Tier 2: Haiku concept extraction fallback (2-4s)
        concept = await self._extract_concept_via_haiku(user_message)
        if concept:
            result = await self._search_and_build(concept)
            if result:
                logger.info("Appetizer tier-2 hit for concept=%r", concept)
                return result

        return None

    async def _search_and_build(self, query: str) -> AppetizerResult | None:
        topics = await self.sefaria_client.search_topics(query, limit=3)
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
```

- [ ] **Step 4: Run keyword extraction tests**

Run: `cd /Users/yotamfromm/dev/sefaria/ai-chatbot && server/venv/bin/python -m pytest server/chat/V2/appetizer/test_appetizer_service.py -k "extract_query" -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add server/chat/V2/appetizer/appetizer_service.py
git commit -m "perf(appetizer): two-tier approach — direct API first, Haiku fallback

Tier 1: regex strips prompt wrappers, sends keywords to Sefaria
name API (<500ms). Covers most prompts.
Tier 2: Haiku extracts concept for abstract prompts (2-4s).
Both run inside 5-second timeout. No Haiku cost for common queries."
```

---

### Task 2: Update Tests for Two-Tier Architecture

**Files:**
- Modify: `server/chat/V2/appetizer/test_appetizer_service.py`

- [ ] **Step 1: Replace old tests with two-tier tests**

Replace the entire file:

```python
"""Tests for the two-tier appetizer pipeline."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ..agent.sefaria_client import SefariaClient
from ..appetizer.appetizer_service import AppetizerService, _extract_query_words


# ---------------------------------------------------------------------------
# _extract_query_words tests
# ---------------------------------------------------------------------------


def test_extract_query_words_strips_common_prefixes():
    assert _extract_query_words("find me sources about Shabbat") == "Shabbat"
    assert _extract_query_words("tell me about the divine attributes") == "divine attributes"
    assert _extract_query_words("what does the Torah say about time") == "time"


def test_extract_query_words_strips_please_variants():
    assert _extract_query_words("please find sources about Rambam") == "Rambam"
    assert _extract_query_words("can you show me texts on prayer") == "prayer"


def test_extract_query_words_preserves_short_queries():
    assert _extract_query_words("Shabbat") == "Shabbat"
    assert _extract_query_words("Rambam") == "Rambam"
    assert _extract_query_words("divine attributes") == "divine attributes"


def test_extract_query_words_handles_hebrew():
    assert _extract_query_words("מה אומרת התורה על שבת") == "מה אומרת התורה על שבת"


def test_extract_query_words_strips_trailing_punctuation():
    assert _extract_query_words("what is Shabbat?") == "Shabbat"
    assert _extract_query_words("tell me about prayer.") == "prayer"


def test_extract_query_words_empty():
    assert _extract_query_words("") == ""
    assert _extract_query_words("   ") == ""


# ---------------------------------------------------------------------------
# search_topics tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_topics_returns_first_match():
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.return_value = {
            "completion_objects": [
                {"title": "Shabbat", "type": "Topic", "key": "shabbat"},
                {"title": "Shabbat HaGadol", "type": "Topic", "key": "shabbat-hagadol"},
            ]
        }
        result = await client.search_topics("shabbat", limit=3)
        assert result == [
            {"title": "Shabbat", "slug": "shabbat"},
            {"title": "Shabbat HaGadol", "slug": "shabbat-hagadol"},
        ]
        mock.assert_called_once_with("api/name/shabbat", {"limit": "3"})


@pytest.mark.asyncio
async def test_search_topics_empty_result():
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.return_value = {"completion_objects": []}
        result = await client.search_topics("xyznonexistent")
        assert result == []


@pytest.mark.asyncio
async def test_search_topics_filters_non_topic_types():
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.return_value = {
            "completion_objects": [
                {"title": "Shabbat", "type": "Topic", "key": "shabbat"},
                {"title": "Shabbat", "type": "TocCategory", "key": "shabbat-cat"},
                {"title": "Shabbat 2a", "type": "ref", "key": "Shabbat.2a"},
            ]
        }
        result = await client.search_topics("shabbat")
        assert len(result) == 1
        assert result[0]["slug"] == "shabbat"


# ---------------------------------------------------------------------------
# AppetizerService — Tier 1 (direct keyword search)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier1_finds_topic_directly():
    """Tier 1 hits: keywords extracted from prompt match a topic via API."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.search_topics.return_value = [
        {"title": "Shabbat", "slug": "shabbat"}
    ]

    result = await service.find_appetizer("find me sources about Shabbat")

    assert result is not None
    assert result.topic_slug == "shabbat"
    assert result.topic_title == "Shabbat"
    assert result.topic_url == "https://www.sefaria.org/topics/shabbat"
    # Tier 1 should call search_topics with the extracted keyword
    service.sefaria_client.search_topics.assert_called_once_with("Shabbat", limit=3)


@pytest.mark.asyncio
async def test_tier1_skips_haiku_when_topic_found():
    """When Tier 1 finds a topic, Haiku is never called."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.search_topics.return_value = [
        {"title": "Shabbat", "slug": "shabbat"}
    ]

    with patch.object(service, "_extract_concept_via_haiku", new_callable=AsyncMock) as mock_haiku:
        result = await service.find_appetizer("find me sources about Shabbat")

    assert result is not None
    mock_haiku.assert_not_called()


# ---------------------------------------------------------------------------
# AppetizerService — Tier 2 (Haiku fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier2_falls_back_to_haiku_when_no_direct_match():
    """Tier 1 misses, Tier 2 uses Haiku to extract concept and finds topic."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    # First call (Tier 1) returns nothing, second call (Tier 2) returns topic
    service.sefaria_client.search_topics.side_effect = [
        [],
        [{"title": "Torah and Secular Wisdom", "slug": "torah-and-secular-wisdom"}],
    ]

    with patch.object(service, "_extract_concept_via_haiku", new_callable=AsyncMock) as mock_haiku:
        mock_haiku.return_value = "Torah and secular wisdom"
        result = await service.find_appetizer("if a person mixes Torah thinking with outside wisdom")

    assert result is not None
    assert result.topic_slug == "torah-and-secular-wisdom"
    mock_haiku.assert_called_once()
    assert service.sefaria_client.search_topics.call_count == 2


@pytest.mark.asyncio
async def test_returns_none_when_both_tiers_miss():
    """Both tiers fail to find a topic — returns None gracefully."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.search_topics.return_value = []

    with patch.object(service, "_extract_concept_via_haiku", new_callable=AsyncMock) as mock_haiku:
        mock_haiku.return_value = "some obscure concept"
        result = await service.find_appetizer("tell me about some obscure thing")

    assert result is None


@pytest.mark.asyncio
async def test_returns_none_when_haiku_returns_none():
    """Haiku says NONE (non-Jewish prompt) — returns None without second search."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()
    service.sefaria_client.search_topics.return_value = []

    with patch.object(service, "_extract_concept_via_haiku", new_callable=AsyncMock) as mock_haiku:
        mock_haiku.return_value = None
        result = await service.find_appetizer("hello how are you?")

    assert result is None
    # Only Tier 1 search, no Tier 2 search since Haiku returned None
    service.sefaria_client.search_topics.assert_called_once()


@pytest.mark.asyncio
async def test_returns_none_on_timeout():
    """Total pipeline timeout (5s) fires — returns None."""
    service = AppetizerService.__new__(AppetizerService)
    service.client = MagicMock()
    service.sefaria_client = AsyncMock()

    async def slow_search(*args, **kwargs):
        await asyncio.sleep(10)
        return [{"title": "X", "slug": "x"}]

    service.sefaria_client.search_topics = slow_search
    result = await service.find_appetizer("test")

    assert result is None
```

- [ ] **Step 2: Run all tests**

Run: `cd /Users/yotamfromm/dev/sefaria/ai-chatbot && server/venv/bin/python -m pytest server/chat/V2/appetizer/test_appetizer_service.py -v`
Expected: All PASS.

- [ ] **Step 3: Commit**

```bash
git add server/chat/V2/appetizer/test_appetizer_service.py
git commit -m "test(appetizer): comprehensive tests for two-tier keyword + Haiku pipeline"
```

---

### Task 3: Restore `limit` Param in search_topics

**Files:**
- Modify: `server/chat/V2/agent/sefaria_client.py:307-318`

The subagent removed the `limit` param during testing because `type=topic` was the actual problem. Now that `type` is gone, `limit` should be restored.

- [ ] **Step 1: Fix search_topics**

Replace the current `search_topics` method:

```python
async def search_topics(self, query: str, limit: int = 5) -> list[dict[str, str]]:
    """Search for topics by name. Returns [{title, slug}, ...]."""
    encoded = quote(query)
    params = {"limit": str(limit)}
    data = await self._get_json(f"api/name/{encoded}", params)
    completions = data.get("completion_objects", [])
    return [
        {"title": c.get("title", ""), "slug": c.get("key", "")}
        for c in completions
        if c.get("type") == "Topic" and c.get("key")
    ]
```

- [ ] **Step 2: Run tests**

Run: `cd /Users/yotamfromm/dev/sefaria/ai-chatbot && server/venv/bin/python -m pytest server/chat/V2/appetizer/test_appetizer_service.py -v`
Expected: All PASS.

- [ ] **Step 3: Commit**

```bash
git add server/chat/V2/agent/sefaria_client.py
git commit -m "fix(sefaria): restore limit param in search_topics without type filter"
```

---

### Task 4: Playwright Verification (use haiku agent)

**Files:**
- Modify: `docs/plans/feature-tests.json`

- [ ] **Step 1: Restart chatbot backend** (picks up new appetizer code)

- [ ] **Step 2: Send discovery prompt and verify Tier 1 speed**

Navigate to `http://localhost:8000/texts`, send "find me sources about Shabbat", poll for `.topic-appetizer` every 1 second. Target: appears in <2 seconds (Tier 1 — no Haiku).

- [ ] **Step 3: Verify TA-2, TA-3, TA-5**

Check link href, collapsibility, persistence after answer.

- [ ] **Step 4: Test Tier 2 with abstract prompt**

Send "if a person mixes Torah thinking with outside wisdom, what does that mean?" — this should NOT match Tier 1, should fall through to Haiku, and still appear within 5 seconds.

- [ ] **Step 5: Test translation suppression (TA-4)**

Send "translate Genesis 1:1" — no appetizer should appear.

- [ ] **Step 6: Update feature-tests.json and commit**

```bash
git add docs/plans/feature-tests.json
git commit -m "test: update feature test results after two-tier appetizer fix"
```
