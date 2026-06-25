# Parallel Haiku Topic Appetizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a relevant Sefaria topic link within 5 seconds of the user's prompt, in parallel with the main agent pipeline, so users have something valuable to explore while waiting for the full answer.

**Architecture:** A lightweight parallel pipeline runs alongside the main agent. After the guardrail passes, two things fire simultaneously: (1) the existing Sonnet agent pipeline, and (2) a fast Haiku call that extracts the key concept from the prompt, finds a matching Sefaria topic via `api/name/{query}?type=topic`, and emits an `appetizer` SSE event with the topic URL and title. The appetizer pipeline has a 5-second hard timeout. Route-based suppression skips the appetizer for Translation prompts.

**Tech Stack:** Python (Django), Anthropic Claude Haiku, Svelte 5, SSE

**Constraint:** The appetizer must reach the client within 5 seconds of prompt submission. This is the headline SLA.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `server/chat/V2/appetizer/` | Package init |
| Create | `server/chat/V2/appetizer/appetizer_service.py` | Haiku topic extraction + Sefaria topic lookup |
| Modify | `server/chat/V2/agent/sefaria_client.py` | Add `search_topics()` method |
| Modify | `server/chat/V2/agent/contracts.py` | Add `appetizer` SSE event type fields |
| Modify | `server/chat/V2/views.py` | Run appetizer in parallel with agent |
| Create | `src/components/TopicAppetizer.svelte` | Yellow box UI for topic display |
| Modify | `src/components/LCChatbot.svelte` | Handle appetizer SSE event, render TopicAppetizer |
| Modify | `src/i18n/locales/en.json` | Appetizer i18n strings |
| Create | `server/chat/V2/appetizer/test_appetizer_service.py` | Tests for appetizer service |

## Context: Existing POC

The `waiting-source` branch (commit `96446de`) shows the first source from the *normal* agent flow in a yellow `SourceSuggestion` box. That approach is limited by the speed of the main pipeline — the first source arrives whenever the agent's first `get_text` tool completes (15-45 seconds). This plan runs a separate, fast pipeline to deliver a topic within 5 seconds.

The two features (SourceSuggestion from `waiting-source` and TopicAppetizer from this plan) can coexist. The topic appetizer appears first (~5s), and if the main pipeline later surfaces a source, that can appear too.

## Context: Sefaria Topic API

- `api/name/{query}?type=topic` — autocomplete that returns matching topic slugs. Already used by `clarify_name_argument` in `sefaria_client.py`.
- `api/v2/topics/{slug}` — full topic details (title, description, image, refs). Already implemented as `get_topic_details`.
- Topic pages on Sefaria: `https://www.sefaria.org/topics/{slug}`

---

### Task 1: Add `search_topics()` to SefariaClient

**Files:**
- Modify: `server/chat/V2/agent/sefaria_client.py:293-304`

- [ ] **Step 1: Write the test**

Create `server/chat/V2/appetizer/__init__.py` (empty) and `server/chat/V2/appetizer/test_appetizer_service.py`:

```python
"""Tests for the appetizer pipeline."""

import pytest
from unittest.mock import AsyncMock, patch

from ..agent.sefaria_client import SefariaClient


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
        mock.assert_called_once_with("api/name/shabbat", {"type": "topic", "limit": "3"})


@pytest.mark.asyncio
async def test_search_topics_empty_result():
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.return_value = {"completion_objects": []}
        result = await client.search_topics("xyznonexistent")
        assert result == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest server/chat/V2/appetizer/test_appetizer_service.py::test_search_topics_returns_first_match -v`
Expected: FAIL — `SefariaClient` has no `search_topics` method.

- [ ] **Step 3: Implement search_topics**

Add after `clarify_name_argument` method (~line 304) in `sefaria_client.py`:

```python
async def search_topics(self, query: str, limit: int = 5) -> list[dict[str, str]]:
    """Search for topics by name. Returns [{title, slug}, ...]."""
    encoded = quote(query)
    params = {"type": "topic", "limit": str(limit)}
    data = await self._get_json(f"api/name/{encoded}", params)
    completions = data.get("completion_objects", [])
    return [
        {"title": c.get("title", ""), "slug": c.get("key", "")}
        for c in completions
        if c.get("type") == "Topic" and c.get("key")
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest server/chat/V2/appetizer/test_appetizer_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/chat/V2/agent/sefaria_client.py server/chat/V2/appetizer/__init__.py server/chat/V2/appetizer/test_appetizer_service.py
git commit -m "feat(sefaria): add search_topics method for topic autocomplete"
```

---

### Task 2: Create AppetizerService

**Files:**
- Create: `server/chat/V2/appetizer/appetizer_service.py`

The service uses Haiku to extract the key concept from the user's prompt, then calls `search_topics` to find a matching Sefaria topic. The entire pipeline must complete within 5 seconds.

- [ ] **Step 1: Write the test**

Add to `test_appetizer_service.py`:

```python
from ..appetizer.appetizer_service import AppetizerService, AppetizerResult


@pytest.mark.asyncio
async def test_appetizer_extracts_topic():
    service = AppetizerService.__new__(AppetizerService)
    service.sefaria_client = AsyncMock()
    service.sefaria_client.search_topics.return_value = [
        {"title": "Divine Attributes", "slug": "divine-attributes"}
    ]

    with patch.object(service, "_extract_concept", new_callable=AsyncMock) as mock_extract:
        mock_extract.return_value = "divine attributes"
        result = await service.find_appetizer("What passage in Micah relates to the 13 attributes?")

    assert result is not None
    assert result.topic_slug == "divine-attributes"
    assert result.topic_title == "Divine Attributes"
    assert "divine-attributes" in result.topic_url


@pytest.mark.asyncio
async def test_appetizer_returns_none_when_no_topics():
    service = AppetizerService.__new__(AppetizerService)
    service.sefaria_client = AsyncMock()
    service.sefaria_client.search_topics.return_value = []

    with patch.object(service, "_extract_concept", new_callable=AsyncMock) as mock_extract:
        mock_extract.return_value = "some obscure thing"
        result = await service.find_appetizer("tell me about some obscure thing")

    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest server/chat/V2/appetizer/test_appetizer_service.py::test_appetizer_extracts_topic -v`
Expected: FAIL — `AppetizerService` does not exist.

- [ ] **Step 3: Implement AppetizerService**

Create `server/chat/V2/appetizer/appetizer_service.py`:

```python
"""Fast topic appetizer — finds a relevant Sefaria topic within 5 seconds."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from django.conf import settings

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
        self.sefaria_client = SefariaClient(base_url=settings.SEFARIA_API_BASE_URL)

    async def find_appetizer(self, user_message: str) -> AppetizerResult | None:
        try:
            return await asyncio.wait_for(
                self._find_appetizer_inner(user_message),
                timeout=APPETIZER_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
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
        if not text or text.upper() == "NONE":
            return None
        return text


get_appetizer_service, reset_appetizer_service = make_singleton(AppetizerService)
```

- [ ] **Step 4: Run tests**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest server/chat/V2/appetizer/ -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add server/chat/V2/appetizer/appetizer_service.py server/chat/V2/appetizer/test_appetizer_service.py
git commit -m "feat(appetizer): add AppetizerService with Haiku topic extraction"
```

---

### Task 3: Add Appetizer SSE Event to Backend

**Files:**
- Modify: `server/chat/V2/agent/contracts.py:11-20`
- Modify: `server/chat/V2/views.py:315-410`

- [ ] **Step 1: Document the new event type**

The `AgentProgressUpdate` already supports arbitrary `type` strings. The appetizer will use type `"appetizer"` with data in a new field. Add to `contracts.py`:

```python
@dataclass
class AgentProgressUpdate:
    """Streamed to the client via SSE during a single chat turn."""

    type: str  # 'status', 'tool_start', 'tool_end', 'complete', 'appetizer'
    text: str | None = None
    tool_name: str | None = None
    tool_input: dict | None = None
    description: str | None = None
    is_error: bool | None = None
    output_preview: str | None = None
    appetizer_data: dict | None = None  # {topicSlug, topicTitle, topicUrl}
```

- [ ] **Step 2: Wire parallel appetizer into views.py**

In the `generate_sse()` function in `views.py`, modify the `run_agent` function to also run the appetizer in parallel. Add a second thread that runs the appetizer and pushes its result to the same `progress_queue`.

After the executor setup (~line 400), before starting the agent thread, add a parallel appetizer thread:

```python
from ..appetizer.appetizer_service import get_appetizer_service, AppetizerResult
from ..router.router_service import RouteType

def run_appetizer():
    """Background thread: fast Haiku topic lookup."""
    try:
        # Skip for translation prompts (router runs before this in the main
        # pipeline, but we can do a cheap deterministic check here)
        from ..router.router_service import RouterService
        if RouterService._deterministic_classify(data["text"]) == RouteType.TRANSLATION:
            return

        appetizer_service = get_appetizer_service()

        async def _find():
            return await appetizer_service.find_appetizer(data["text"])

        result = asyncio.run(_find())
        if result and not stream_closed:
            update = AgentProgressUpdate(
                type="appetizer",
                text=result.topic_title,
                appetizer_data={
                    "topicSlug": result.topic_slug,
                    "topicTitle": result.topic_title,
                    "topicUrl": result.topic_url,
                },
            )
            try:
                progress_queue.put(update, timeout=0.5)
            except queue.Full:
                pass
    except Exception:
        logger.exception("Appetizer thread failed")

# Start appetizer in parallel (separate thread, not blocking agent)
appetizer_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
appetizer_executor.submit(run_appetizer)
```

- [ ] **Step 3: Add appetizer_data to SSE payload serialization**

In the SSE event builder (~line 420-438), add:

```python
if update.appetizer_data:
    event_data["appetizerData"] = update.appetizer_data
```

- [ ] **Step 4: Verify manually**

Start the server: `python manage.py runserver 0.0.0.0:8001`
Send a prompt via curl and inspect SSE events:

```bash
curl -N -X POST http://localhost:8001/api/v2/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"userId":"test","sessionId":"test","messageId":"m1","timestamp":"2026-01-01","text":"find me sources about Shabbat","context":{"pageUrl":"/","locale":"en"}}' 2>&1 | head -20
```

Expected: An `event: progress` with `type: "appetizer"` should appear within ~5 seconds.

- [ ] **Step 5: Commit**

```bash
git add server/chat/V2/agent/contracts.py server/chat/V2/views.py
git commit -m "feat(appetizer): run Haiku topic lookup in parallel, emit SSE appetizer event"
```

---

### Task 4: Create TopicAppetizer Frontend Component

**Files:**
- Create: `src/components/TopicAppetizer.svelte`

- [ ] **Step 1: Create the component**

```svelte
<script>
  import { _ } from '../i18n/index.js';

  let { data, streaming = false } = $props();

  let expanded = $state(true);

  function toggle() {
    if (!streaming) expanded = !expanded;
  }
</script>

<div class="topic-appetizer">
  <button
    class="appetizer-header"
    onclick={toggle}
    aria-expanded={expanded}
  >
    <svg class="appetizer-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="12" cy="12" r="10"></circle>
      <line x1="12" y1="16" x2="12" y2="12"></line>
      <line x1="12" y1="8" x2="12.01" y2="8"></line>
    </svg>
    <span class="appetizer-header-text">
      {$_('appetizer.whileWaiting')}
    </span>
    <svg class="appetizer-chevron" class:rotated={!expanded} width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
      <polyline points="6 9 12 15 18 9"></polyline>
    </svg>
  </button>

  {#if expanded}
    <div class="appetizer-body">
      <a
        class="appetizer-link"
        href={data.topicUrl}
        target="_blank"
        rel="noopener noreferrer"
        data-appetizer-topic={data.topicSlug}
      >
        {data.topicTitle} →
      </a>
      <span class="appetizer-hint">{$_('appetizer.exploreHint')}</span>
    </div>
  {/if}
</div>
```

- [ ] **Step 2: Commit**

```bash
git add src/components/TopicAppetizer.svelte
git commit -m "feat(TopicAppetizer): add yellow box component for topic display"
```

---

### Task 5: Wire TopicAppetizer into LCChatbot

**Files:**
- Modify: `src/components/LCChatbot.svelte`
- Modify: `src/i18n/locales/en.json`

- [ ] **Step 1: Add import and state**

```javascript
import TopicAppetizer from './TopicAppetizer.svelte';

// In state section
let appetizerData = $state(null);
let appetizerMessageId = $state(null);
```

- [ ] **Step 2: Handle appetizer SSE event in onProgress**

Add to the `onProgress` handler, before the existing `if/else` chain:

```javascript
if (progress?.type === 'appetizer' && progress.appetizerData) {
  appetizerData = progress.appetizerData;
  return;
}
```

- [ ] **Step 3: Reset appetizer on new message**

In the `sendMessage` function, after `currentProgress = null`:

```javascript
appetizerData = null;
appetizerMessageId = null;
```

- [ ] **Step 4: Render TopicAppetizer during streaming**

In the `{#if isSending}` block, before the ProgressTrail (or thinking bubble):

```svelte
{#if isSending}
  <div class="message assistant">
    {#if appetizerData}
      <TopicAppetizer data={appetizerData} streaming={true} />
    {/if}
    <!-- existing thinking/progress display -->
  </div>
{/if}
```

- [ ] **Step 5: Persist appetizer and show collapsed after answer**

When building `assistantMessage` on success:
```javascript
const assistantMessage = {
  // ... existing fields ...
  appetizerData: appetizerData ? {...appetizerData} : null,
};
```

And in the message list rendering:
```svelte
{:else if item.role === 'assistant'}
  {#if item.appetizerData}
    <TopicAppetizer data={item.appetizerData} streaming={false} />
  {/if}
```

- [ ] **Step 6: Add click tracking**

Add a click handler on the appetizer link to dispatch an analytics event:

```javascript
function handleAppetizerClick(topicSlug) {
  dispatchEvent(new CustomEvent('appetizer_click', {
    detail: { topicSlug, sessionId },
    bubbles: true, composed: true
  }));
}
```

Wire it in TopicAppetizer via an `onclick` prop.

- [ ] **Step 7: Add i18n strings**

In `en.json`:

```json
"appetizer.whileWaiting": "While we're working on your answer, you might find this interesting",
"appetizer.exploreHint": "Explore this topic on Sefaria",
```

- [ ] **Step 8: Commit**

```bash
git add src/components/LCChatbot.svelte src/components/TopicAppetizer.svelte src/i18n/locales/en.json
git commit -m "feat: wire TopicAppetizer into chat UI with click tracking"
```

---

### Task 6: Add CSS for TopicAppetizer

**Files:**
- Modify: `src/components/LCChatbot.svelte` (style section)

- [ ] **Step 1: Add :global() styles**

```css
:global(.topic-appetizer) {
  margin: 6px 12px 6px;
  border-radius: 8px;
  border: 1px solid #e9d96a;
  background: #fffde7;
  font-size: 13px;
  font-family: inherit;
  animation: appetizer-fade-in 0.3s ease;
}
@keyframes appetizer-fade-in {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}
:global(.appetizer-header) {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 8px 10px;
  background: none;
  border: none;
  cursor: pointer;
  text-align: start;
  color: #4a4700;
  font-size: 12px;
  font-family: inherit;
}
:global(.appetizer-header:hover) {
  background: #fff9c4;
  border-radius: 8px;
}
:global(.appetizer-icon) {
  flex-shrink: 0;
  color: #8a7a00;
}
:global(.appetizer-header-text) {
  flex: 1;
  font-weight: 500;
}
:global(.appetizer-chevron) {
  flex-shrink: 0;
  color: #8a7a00;
  transition: transform 0.2s ease;
}
:global(.appetizer-chevron.rotated) {
  transform: rotate(-90deg);
}
:global(.appetizer-body) {
  padding: 6px 10px 10px;
  border-top: 1px solid #f0e68c;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
:global(.appetizer-link) {
  color: #18345D;
  text-decoration: underline;
  font-size: 14px;
  font-weight: 600;
}
:global(.appetizer-link:hover) {
  color: #465D7D;
}
:global(.appetizer-hint) {
  color: #8a7a00;
  font-size: 11px;
}
```

- [ ] **Step 2: Commit**

```bash
git add src/components/LCChatbot.svelte
git commit -m "style: add yellow box CSS for TopicAppetizer"
```

---

### Task 7: Build Verification and Integration Test

- [ ] **Step 1: Run backend tests**

```bash
DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest server/chat/V2/appetizer/ -v
```

Expected: All tests pass.

- [ ] **Step 2: Run frontend build**

```bash
npm run build
```

Expected: Build succeeds.

- [ ] **Step 3: Full manual test**

Start both servers:
```bash
./start.sh
```

Test scenarios:
1. **Discovery prompt** ("find me sources about Shabbat") — yellow topic box appears within 5s, then full answer comes later
2. **Translation prompt** ("translate this passage") — NO appetizer appears (suppressed)
3. **Esoteric prompt** ("What passage in Micah did Moshe Cordovaro interpret as relating to the 13 attributes?") — appetizer shows a relevant topic
4. **Non-Jewish prompt** ("hello, how are you?") — NO appetizer (Haiku returns NONE)
5. **Verify click tracking** — click the topic link, check that `appetizer_click` event fires

- [ ] **Step 4: Commit any fixes**

---

## Design Decisions

1. **Parallel, not sequential:** The appetizer runs in its own thread, completely independent of the main agent. If it takes >5s, it's silently dropped. The main pipeline is never delayed.

2. **Deterministic translation suppression:** Rather than waiting for the full router LLM call, we reuse `RouterService._deterministic_classify()` (regex check on first 3 words) for the appetizer gate. This is instant and covers the most obvious translation cases. Imperfect, but fast.

3. **Haiku for concept extraction:** A single Haiku call extracts the key concept, then a Sefaria API call finds the topic. Two steps, but both are fast (~1-2s for Haiku, <1s for the API).

4. **Topic over source:** The meeting concluded that a topic page is a "richer artifact" than a single source. Topics have descriptions, images, curated sources. This is the appetizer's value proposition.

5. **Coexistence with SourceSuggestion:** The POC's yellow source box (`waiting-source` branch) and this topic appetizer can coexist. The topic arrives first (~5s), the source arrives later when the main pipeline finds one. They serve different purposes.

## Future Considerations

- **Full router integration:** Replace the deterministic suppression with a proper route check once the router is fast enough or runs in parallel.
- **Appetizer quality evaluation:** Track click-through rates in Braintrust to measure whether users find the appetizer useful.
- **Topic + description:** Consider fetching the topic description (via `get_topic_details`) to show a one-line preview in the yellow box. Adds ~1s but makes the appetizer more informative.
- **Hebrew support:** Add `he.json` translations for appetizer strings once the UX copy is finalized.
