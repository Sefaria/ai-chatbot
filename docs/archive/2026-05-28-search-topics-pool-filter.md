# search_topics Pool Filter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional `pool` parameter to `search_topics()` so callers can restrict results to topics belonging to a specific Sefaria pool (e.g. `"library"`).

> **IMPLEMENTED 2026-06-25 — approach revised.** The original assumption below (that `api/v2/topics/{slug}` exposes a `pools` list) was DISPROVEN against the live API: that endpoint has no pool field. Pool membership is actually returned by the `api/name/` autocomplete itself, on each completion object as **`topic_pools`** (e.g. shabbat → `['torah_tab','library','general_he','sheets','general_en']`, daf-yomi → `['sheets']`). The shipped implementation therefore filters candidates by `'library' in completion['topic_pools']` (zero extra API calls), and — to fix the separate title/page-mismatch feedback — overrides each kept candidate's title with the canonical `primaryTitle.en` from `api/v2/topics/{slug}`. When `pool` is set the slug-fallback is skipped entirely (this is what eliminated the bare "parashat" leak). A `good_to_promote` approach was tried first and rejected because it over-filters known-good topics (Herod, Parenting, Temple all have `good_to_promote=None`).

**Architecture (original draft — superseded, see note above):** `SefariaClient.search_topics()` currently uses the `api/name/` autocomplete endpoint, which returns topic type but not pool membership. We add a `_get_topic_pools(slug)` helper that fetches `api/v2/topics/{slug}` and extracts the raw `pools` list, then use it to post-filter candidates when `pool` is specified. The slug-fallback path already fetches the full topic document, so it can check pools directly without an extra call.

**Tech Stack:** Python 3.11+, asyncio, httpx (via `SefariaClient._get_json`), pytest-asyncio

---

## File Map

| Action | File | What changes |
|--------|------|-------------|
| Modify | `server/chat/V2/agent/sefaria_client.py` | Add `_get_topic_pools()`, add `pool` param to `search_topics()` |
| Modify | `server/chat/V2/appetizer/test_appetizer_service.py` | Add pool-filter tests for `search_topics()` |

---

## Context You Need

**`search_topics()` lives at** `server/chat/V2/agent/sefaria_client.py:307`.

Current signature:
```python
async def search_topics(self, query: str, limit: int = 5) -> list[dict[str, str]]:
```

It has two code paths:
1. **Name API path** — calls `api/name/{query}`, filters completions by type (`Topic`, `PersonTopic`, `AuthorTopic`), returns `[{"title": ..., "slug": ...}, ...]`.
2. **Slug fallback** — if the name API returns nothing, calls `api/v2/topics/{slug}` directly and builds a single-item result.

The Sefaria `api/v2/topics/{slug}` response includes a `pools` field — a list of string pool names (e.g. `["library", "sheets"]`). Pool names in use: `library`, `sheets`, `torah_tab`, `general_en`, `general_he`.

> **Assumption to verify before Task 1:** The raw `api/v2/topics/{slug}` JSON has a top-level `"pools"` key that is a list of strings. Confirm this against a live response (e.g. `curl https://www.sefaria.org/api/v2/topics/shabbat | jq '.pools'`) before writing code. If the field name differs, update every reference in this plan.

**Existing test file:** `server/chat/V2/appetizer/test_appetizer_service.py` — run with `pytest server/chat/V2/appetizer/test_appetizer_service.py -v`.

---

## Task 1: Verify pool field name in Sefaria API

**Files:**
- Read-only check, no code changes

- [ ] **Step 1: Inspect the live API response**

```bash
curl -s "https://www.sefaria.org/api/v2/topics/shabbat" | python3 -m json.tool | grep -A5 '"pools"'
```

Expected: a `"pools"` key with a list of strings like `["library", "sheets"]`.

If the field is named differently (e.g. `"pool"`, `"topic_pools"`), update every occurrence in Tasks 2–3 before proceeding.

---

## Task 2: Add `_get_topic_pools()` helper

**Files:**
- Modify: `server/chat/V2/agent/sefaria_client.py` (add method after `search_topics`)
- Modify: `server/chat/V2/appetizer/test_appetizer_service.py` (add tests)

- [ ] **Step 1: Write the failing test**

Add to the end of `server/chat/V2/appetizer/test_appetizer_service.py`:

```python
# ---------------------------------------------------------------------------
# _get_topic_pools tests (SefariaClient)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_topic_pools_returns_list():
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.return_value = {"slug": "shabbat", "pools": ["library", "sheets"]}
        result = await client._get_topic_pools("shabbat")
        assert result == ["library", "sheets"]
        mock.assert_called_once_with("api/v2/topics/shabbat")


@pytest.mark.asyncio
async def test_get_topic_pools_missing_field_returns_empty():
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.return_value = {"slug": "shabbat"}  # no pools key
        result = await client._get_topic_pools("shabbat")
        assert result == []


@pytest.mark.asyncio
async def test_get_topic_pools_exception_returns_empty():
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.side_effect = Exception("network error")
        result = await client._get_topic_pools("shabbat")
        assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd server && DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/V2/appetizer/test_appetizer_service.py::test_get_topic_pools_returns_list chat/V2/appetizer/test_appetizer_service.py::test_get_topic_pools_missing_field_returns_empty chat/V2/appetizer/test_appetizer_service.py::test_get_topic_pools_exception_returns_empty -v
```

Expected: `AttributeError: 'SefariaClient' object has no attribute '_get_topic_pools'`

- [ ] **Step 3: Implement `_get_topic_pools()`**

In `server/chat/V2/agent/sefaria_client.py`, add this method directly after `search_topics` (around line 334):

```python
async def _get_topic_pools(self, slug: str) -> list[str]:
    try:
        data = await self._get_json(f"api/v2/topics/{quote(slug)}")
        pools = data.get("pools", [])
        return pools if isinstance(pools, list) else []
    except Exception:
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd server && DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/V2/appetizer/test_appetizer_service.py::test_get_topic_pools_returns_list chat/V2/appetizer/test_appetizer_service.py::test_get_topic_pools_missing_field_returns_empty chat/V2/appetizer/test_appetizer_service.py::test_get_topic_pools_exception_returns_empty -v
```

Expected: all 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add server/chat/V2/agent/sefaria_client.py server/chat/V2/appetizer/test_appetizer_service.py
git commit -m "feat: add _get_topic_pools() helper to SefariaClient"
```

---

## Task 3: Add `pool` parameter to `search_topics()`

**Files:**
- Modify: `server/chat/V2/agent/sefaria_client.py` (`search_topics` method)
- Modify: `server/chat/V2/appetizer/test_appetizer_service.py` (add tests)

- [ ] **Step 1: Write the failing tests**

Add to `server/chat/V2/appetizer/test_appetizer_service.py`:

```python
# ---------------------------------------------------------------------------
# search_topics — pool filtering
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_topics_pool_filter_keeps_matching():
    """Topics in the requested pool are returned."""
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        # name API returns one topic
        mock.side_effect = [
            {"completion_objects": [{"title": "Shabbat", "type": "Topic", "key": "shabbat"}]},
            {"slug": "shabbat", "pools": ["library", "sheets"]},  # pool check
        ]
        result = await client.search_topics("shabbat", pool="library")
        assert result == [{"title": "Shabbat", "slug": "shabbat"}]


@pytest.mark.asyncio
async def test_search_topics_pool_filter_removes_non_matching():
    """Topics not in the requested pool are filtered out."""
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.side_effect = [
            {"completion_objects": [{"title": "My Sheet Topic", "type": "Topic", "key": "my-sheet-topic"}]},
            {"slug": "my-sheet-topic", "pools": ["sheets"]},  # not in library
        ]
        result = await client.search_topics("my-sheet-topic", pool="library")
        assert result == []


@pytest.mark.asyncio
async def test_search_topics_no_pool_filter_skips_pool_check():
    """When pool=None, no extra API call is made."""
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.return_value = {
            "completion_objects": [{"title": "Shabbat", "type": "Topic", "key": "shabbat"}]
        }
        result = await client.search_topics("shabbat")
        assert result == [{"title": "Shabbat", "slug": "shabbat"}]
        assert mock.call_count == 1  # only the name API call, no pool check


@pytest.mark.asyncio
async def test_search_topics_slug_fallback_respects_pool():
    """When the slug fallback is used, pool is still checked."""
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.side_effect = [
            {"completion_objects": []},  # name API misses
            {"slug": "shabbat", "primaryTitle": {"en": "Shabbat"}, "pools": ["sheets"]},  # slug fetch, wrong pool
        ]
        result = await client.search_topics("Shabbat", pool="library")
        assert result == []


@pytest.mark.asyncio
async def test_search_topics_slug_fallback_passes_pool_when_matching():
    """Slug fallback returns the topic when pool matches."""
    client = SefariaClient(base_url="https://www.sefaria.org")
    with patch.object(client, "_get_json", new_callable=AsyncMock) as mock:
        mock.side_effect = [
            {"completion_objects": []},  # name API misses
            {"slug": "shabbat", "primaryTitle": {"en": "Shabbat"}, "pools": ["library", "sheets"]},
        ]
        result = await client.search_topics("Shabbat", pool="library")
        assert result == [{"title": "Shabbat", "slug": "shabbat"}]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd server && DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/V2/appetizer/test_appetizer_service.py::test_search_topics_pool_filter_keeps_matching chat/V2/appetizer/test_appetizer_service.py::test_search_topics_pool_filter_removes_non_matching chat/V2/appetizer/test_appetizer_service.py::test_search_topics_no_pool_filter_skips_pool_check chat/V2/appetizer/test_appetizer_service.py::test_search_topics_slug_fallback_respects_pool chat/V2/appetizer/test_appetizer_service.py::test_search_topics_slug_fallback_passes_pool_when_matching -v
```

Expected: `TypeError: search_topics() got an unexpected keyword argument 'pool'`

- [ ] **Step 3: Update `search_topics()` signature and body**

Replace the existing `search_topics` method in `server/chat/V2/agent/sefaria_client.py`:

```python
async def search_topics(
    self, query: str, limit: int = 5, pool: str | None = None
) -> list[dict[str, str]]:
    """Search for topics by name. Returns [{title, slug}, ...].

    Tries the name autocomplete API first. If no topics are found, falls
    back to a direct slug lookup (e.g. "Shabbat" matches the tractate in
    autocomplete but the topic exists at api/v2/topics/shabbat).

    If pool is given, only topics belonging to that pool are returned.
    This requires an extra api/v2/topics/{slug} call per candidate from
    the name API path.
    """
    encoded = quote(query)
    params = {"limit": str(limit)}
    data = await self._get_json(f"api/name/{encoded}", params)
    completions = data.get("completion_objects", [])
    candidates = [
        {"title": c.get("title", ""), "slug": c.get("key", "")}
        for c in completions
        if c.get("type") in {"Topic", "PersonTopic", "AuthorTopic"} and c.get("key")
    ]

    if pool:
        filtered = []
        for candidate in candidates:
            pools = await self._get_topic_pools(candidate["slug"])
            if pool in pools:
                filtered.append(candidate)
        candidates = filtered

    if candidates:
        return candidates

    slug = query.lower().replace(" ", "-")
    try:
        topic_data = await self._get_json(f"api/v2/topics/{quote(slug)}")
        if topic_data.get("slug"):
            if pool and pool not in topic_data.get("pools", []):
                return []
            title = topic_data.get("primaryTitle", {}).get("en", query)
            return [{"title": title, "slug": topic_data["slug"]}]
    except Exception:
        pass
    return []
```

- [ ] **Step 4: Run all pool-filter tests to verify they pass**

```bash
cd server && DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/V2/appetizer/test_appetizer_service.py::test_search_topics_pool_filter_keeps_matching chat/V2/appetizer/test_appetizer_service.py::test_search_topics_pool_filter_removes_non_matching chat/V2/appetizer/test_appetizer_service.py::test_search_topics_no_pool_filter_skips_pool_check chat/V2/appetizer/test_appetizer_service.py::test_search_topics_slug_fallback_respects_pool chat/V2/appetizer/test_appetizer_service.py::test_search_topics_slug_fallback_passes_pool_when_matching -v
```

Expected: all 5 PASS.

- [ ] **Step 5: Run the full test file to verify no regressions**

```bash
cd server && DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest chat/V2/appetizer/test_appetizer_service.py -v
```

Expected: all tests pass (the 10 pre-existing tests + 8 new ones = 18 total).

- [ ] **Step 6: Commit**

```bash
git add server/chat/V2/agent/sefaria_client.py server/chat/V2/appetizer/test_appetizer_service.py
git commit -m "feat: add optional pool filter to search_topics()"
```

---

## Self-Review

**Spec coverage:**
- `_get_topic_pools()` helper — ✅ Task 2
- `pool` param on `search_topics()` — ✅ Task 3
- Name API path filtering — ✅ Task 3, step 3 (`filtered` loop)
- Slug fallback filtering — ✅ Task 3, step 3 (pool check in fallback block)
- No extra call when `pool=None` — ✅ tested in `test_search_topics_no_pool_filter_skips_pool_check`
- Exception safety in pool fetch — ✅ Task 2, `test_get_topic_pools_exception_returns_empty`

**Placeholders:** None.

**Type consistency:** `pool: str | None = None` used consistently in signature and body. `_get_topic_pools` returns `list[str]` throughout.
