# Ref API Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fragile client-side Sefaria ref parsing with the canonical `/api/ref/<tref>` endpoint, render refs bilingually (HE/EN) and RTL-safe, and apply two coding conventions to all touched code.

**Architecture:** The backend resolves ref-bearing tool args via `/api/ref` and attaches `{is_ref, url_ref, en, he}` to the `tool_start` SSE event; the frontend renders the provided data and deletes all client-side ref parsing. The location pin resolves its own ref via `/api/ref` from the browser. Hebrew labels are bidi-isolated inside the LTR trail.

**Tech Stack:** Django + httpx (backend), Svelte 5 web component + Vite (frontend), pytest (backend tests).

**Spec:** `docs/plans/2026-06-22-ref-api-migration.md`

## Global Constraints

- **No one-line function bodies** — every function body spans multiple lines (never `function x() { return y }` on one line).
- **No nested `if`s** — use early returns / guard clauses / combined conditions.
- **Use existing design tokens** from the `:host` block in `LCChatbot.svelte`; no hardcoded colors/sizes; no redundant `var(--x, fallback)` for our own tokens.
- **CSS logical properties only** (`start/end`, `*-inline-*`); no physical `left/right`.
- **Commits:** Conventional Commits (`feat`/`fix`/`refactor`/`docs`/`test`/`chore`). End commit messages with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- **Backend tests:** `cd server && source venv/bin/activate && DJANGO_SETTINGS_MODULE=chatbot_server.test_settings python -m pytest <path> -q`.
- **Frontend verification:** `npm run build` must succeed (pre-existing HeaderButton custom-element warning is OK); manual verify in EN and HE. This repo has no frontend unit tests by convention.
- Backend Sefaria base URL comes from `SefariaClient.base_url` (env `SEFARIA_API_BASE_URL`); the frontend pin uses `https://www.sefaria.org` directly.

---

### Task 1: Backend — `SefariaClient.resolve_ref`

**Files:**
- Modify: `server/chat/V2/agent/sefaria_client.py` (add method + per-instance cache field in `__init__`)
- Test: `server/chat/V2/agent/test_sefaria_client.py` (or the existing client test module — confirm with `ls server/chat/V2/agent/test_*.py` and `grep -rl "SefariaClient" server/chat`)

**Interfaces:**
- Produces: `async SefariaClient.resolve_ref(tref: str) -> dict | None`, returning `{"is_ref": True, "url_ref": str, "en": str, "he": str}` for valid refs, `None` for invalid/error/empty. Cached per instance.

- [ ] **Step 1: Write the failing test**

```python
# in the client test module
import pytest
from unittest.mock import AsyncMock
from chat.V2.agent.sefaria_client import SefariaClient

@pytest.mark.asyncio
async def test_resolve_ref_valid():
    client = SefariaClient()
    client._get_json = AsyncMock(return_value={
        "is_ref": True, "normalized": "Genesis 1:1",
        "hebrew": "בראשית א׳:א׳", "url_ref": "Genesis.1.1",
    })
    result = await client.resolve_ref("Genesis 1:1")
    assert result == {
        "is_ref": True, "url_ref": "Genesis.1.1",
        "en": "Genesis 1:1", "he": "בראשית א׳:א׳",
    }

@pytest.mark.asyncio
async def test_resolve_ref_invalid_returns_none():
    client = SefariaClient()
    client._get_json = AsyncMock(return_value={"is_ref": False})
    assert await client.resolve_ref("not a ref") is None

@pytest.mark.asyncio
async def test_resolve_ref_error_returns_none():
    import httpx
    client = SefariaClient()
    client._get_json = AsyncMock(side_effect=httpx.HTTPError("boom"))
    assert await client.resolve_ref("Genesis 1:1") is None

@pytest.mark.asyncio
async def test_resolve_ref_caches():
    client = SefariaClient()
    client._get_json = AsyncMock(return_value={
        "is_ref": True, "normalized": "Genesis 1:1",
        "hebrew": "x", "url_ref": "Genesis.1.1",
    })
    await client.resolve_ref("Genesis 1:1")
    await client.resolve_ref("Genesis 1:1")
    assert client._get_json.call_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && source venv/bin/activate && DJANGO_SETTINGS_MODULE=chatbot_server.test_settings python -m pytest chat/V2/agent/test_sefaria_client.py -k resolve_ref -q`
Expected: FAIL (`AttributeError: 'SefariaClient' object has no attribute 'resolve_ref'`)

- [ ] **Step 3: Write minimal implementation**

In `__init__` (alongside `self._client = None`), add:
```python
        self._ref_cache: dict[str, dict | None] = {}
```
Add the methods (multi-line bodies, no nested ifs):
```python
    async def resolve_ref(self, tref: str) -> dict | None:
        """Resolve a tref via /api/ref into {is_ref, url_ref, en, he}, or None.

        Results (including negatives) are cached per instance to avoid
        redundant lookups of repeated refs within a turn.
        """
        if not tref:
            return None
        if tref in self._ref_cache:
            return self._ref_cache[tref]
        result = await self._fetch_ref(tref)
        if len(self._ref_cache) < 512:
            self._ref_cache[tref] = result
        return result

    async def _fetch_ref(self, tref: str) -> dict | None:
        encoded_ref = quote(tref)
        try:
            data = await self._get_json(f"api/ref/{encoded_ref}")
        except (httpx.HTTPError, ValueError):
            return None
        if not isinstance(data, dict) or not data.get("is_ref"):
            return None
        return {
            "is_ref": True,
            "url_ref": data.get("url_ref", ""),
            "en": data.get("normalized", ""),
            "he": data.get("hebrew", ""),
        }
```
(`quote` and `httpx` are already imported in this module.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd server && source venv/bin/activate && DJANGO_SETTINGS_MODULE=chatbot_server.test_settings python -m pytest chat/V2/agent/test_sefaria_client.py -k resolve_ref -q`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add server/chat/V2/agent/sefaria_client.py server/chat/V2/agent/test_sefaria_client.py
git commit -m "feat: add SefariaClient.resolve_ref via /api/ref with caching"
```

---

### Task 2: Backend — enrich `tool_start` events with `ref_data`

**Files:**
- Modify: `server/chat/V2/agent/contracts.py` (add field to `AgentProgressUpdate`)
- Modify: `server/chat/V2/agent/tool_executor.py` (add `REF_TOOL_ARG` map + `resolve_tool_ref` helper, near `describe_tool_call`)
- Modify: `server/chat/V2/agent/tool_runtime.py` (call helper, attach to `tool_start`)
- Modify: `server/chat/V2/views.py` (serialize `ref_data` → `refData` in the SSE payload)
- Test: `server/chat/V2/agent/test_tool_executor.py` (existing — has `describe_tool_call` tests)

**Interfaces:**
- Consumes: `SefariaClient.resolve_ref` (Task 1).
- Produces: `async resolve_tool_ref(client, tool_name: str, tool_input: dict) -> dict | None`; `AgentProgressUpdate.ref_data: dict | None`; SSE event key `refData`.

- [ ] **Step 1: Write the failing test** (in `test_tool_executor.py`)

```python
import pytest
from unittest.mock import AsyncMock
from chat.V2.agent.tool_executor import resolve_tool_ref

@pytest.mark.asyncio
async def test_resolve_tool_ref_for_get_text():
    client = AsyncMock()
    client.resolve_ref = AsyncMock(return_value={
        "is_ref": True, "url_ref": "Genesis.1.1", "en": "Genesis 1:1", "he": "x",
    })
    result = await resolve_tool_ref(client, "get_text", {"reference": "Genesis 1:1"})
    assert result["url_ref"] == "Genesis.1.1"
    client.resolve_ref.assert_awaited_once_with("Genesis 1:1")

@pytest.mark.asyncio
async def test_resolve_tool_ref_for_non_ref_tool():
    client = AsyncMock()
    client.resolve_ref = AsyncMock()
    result = await resolve_tool_ref(client, "text_search", {"query": "shabbat"})
    assert result is None
    client.resolve_ref.assert_not_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd server && source venv/bin/activate && DJANGO_SETTINGS_MODULE=chatbot_server.test_settings python -m pytest chat/V2/agent/test_tool_executor.py -k resolve_tool_ref -q`
Expected: FAIL (`ImportError: cannot import name 'resolve_tool_ref'`)

- [ ] **Step 3: Write minimal implementation**

In `contracts.py`, add to `AgentProgressUpdate` (after `appetizer_data`):
```python
    ref_data: dict | None = None  # {is_ref, url_ref, en, he} for ref-bearing tools
```

In `tool_executor.py`, near `describe_tool_call`:
```python
# Tools whose named arg is a Sefaria ref (resolved via /api/ref for the trail link).
REF_TOOL_ARG = {
    "get_text": "reference",
    "get_links_between_texts": "reference",
    "get_available_manuscripts": "reference",
    "get_english_translations": "reference",
}


async def resolve_tool_ref(client, tool_name: str, tool_input: dict) -> dict | None:
    """Resolve the ref arg of a ref-bearing tool to {is_ref, url_ref, en, he}, or None."""
    arg_name = REF_TOOL_ARG.get(tool_name)
    if not arg_name:
        return None
    ref = tool_input.get(arg_name)
    if not ref:
        return None
    return await client.resolve_ref(ref)
```

In `tool_runtime.py` `build_handler.handler`, locate the `SefariaClient` instance (the executor holds it — confirm the attribute via `grep -n "sefaria_client\|SefariaClient\|self.client" server/chat/V2/agent/tool_executor.py`). Then, before the `tool_start` emit:
```python
                ref_data = await resolve_tool_ref(
                    self.tool_executor.sefaria_client, tool_name, tool_input
                )

                emit(
                    AgentProgressUpdate(
                        type="tool_start",
                        tool_name=tool_name,
                        tool_input=tool_input,
                        description=tool_desc,
                        ref_data=ref_data,
                    )
                )
```
(Add `from chat.V2.agent.tool_executor import resolve_tool_ref` — match the module's existing import style. Adjust `self.tool_executor.sefaria_client` to the real attribute name found by the grep above.)

In `views.py`, in the `event_data` block (after the `appetizerData` line):
```python
                    if update.ref_data:
                        event_data["refData"] = update.ref_data
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd server && source venv/bin/activate && DJANGO_SETTINGS_MODULE=chatbot_server.test_settings python -m pytest chat/V2/agent/test_tool_executor.py -q`
Expected: all pass (new + existing describe_tool_call tests)

- [ ] **Step 5: Commit**

```bash
git add server/chat/V2/agent/contracts.py server/chat/V2/agent/tool_executor.py server/chat/V2/agent/tool_runtime.py server/chat/V2/views.py server/chat/V2/agent/test_tool_executor.py
git commit -m "feat: enrich tool_start events with resolved ref data"
```

---

### Task 3: Frontend — render trail refs from `refData`, delete client parsing

**Files:**
- Modify: `src/lib/api.js` (document `refData` on the ProgressEvent typedef; data already passes through verbatim)
- Modify: `src/components/LCChatbot.svelte` (`onProgress` `tool_start`: store `refData` on the entry; `finalTrail` keeps it)
- Modify: `src/components/ProgressTrail.svelte` (delete parsers; render from `refData` with bidi)

**Interfaces:**
- Consumes: SSE `tool_start` event with `refData = {is_ref, url_ref, en, he}` (Task 2).
- Produces: `toolHistory` entries with optional `refData`; trail links rendered from it.

- [ ] **Step 1: Thread `refData` into the entry** — in `LCChatbot.svelte` `onProgress`, the `tool_start` branch that pushes to `toolHistory`, add `refData: progress.refData ?? null` to the pushed object. Confirm `finalTrail`'s `.map(...)` spreads the whole entry (`{ ...t, ... }`) so `refData` is retained; if it builds a fresh object, add `refData: t.refData`.

- [ ] **Step 2: Add the interface-language + base-url helpers to `ProgressTrail.svelte`**

```js
  import { locale } from '../i18n/index.js';

  const SEFARIA_BASE_URL = 'https://www.sefaria.org';

  let isHebrew = $derived($locale === 'he');

  /** Locale-appropriate label for a resolved ref. */
  function refDisplayLabel(refData) {
    if (isHebrew && refData.he) {
      return refData.he;
    }
    return refData.en;
  }
```

- [ ] **Step 3: Delete the client-side parsers**

Remove `refToUrl`, `refLabel`, `substituteRefs`, `linkifyRefs`, `plainRefs`, and the old `SEFARIA_BASE_URL` const (re-added in Step 2) from `ProgressTrail.svelte`.

- [ ] **Step 4: Render the entry text with an optional bidi-isolated link**

Add a helper that builds the safe HTML for a tool entry, replacing the known ref substring with a link:
```js
  function escapeHtml(text) {
    return text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  /**
   * Render a tool entry's description. When refData.is_ref, replace the
   * known ref substring (the tool's reference arg) with a bidi-isolated link.
   */
  function renderToolText(entry) {
    const text = entry.description ?? entry.toolName ?? '';
    const refData = entry.refData;
    if (!refData || !refData.is_ref) {
      return escapeHtml(text);
    }
    const rawRef = entry.toolInput?.reference ?? '';
    const escaped = escapeHtml(text);
    const escapedRef = escapeHtml(rawRef);
    if (!escapedRef || !escaped.includes(escapedRef)) {
      return escaped;
    }
    const href = `${SEFARIA_BASE_URL}/${refData.url_ref}`;
    const label = escapeHtml(refDisplayLabel(refData));
    const link = `<a class="trail-ref-link" href="${href}" target="_blank" rel="noopener noreferrer"><bdi>${label}</bdi></a>`;
    return escaped.replace(escapedRef, link);
  }
```
(`entry.toolInput` requires the `tool_start` handler in `LCChatbot.svelte` to also store `toolInput: progress.toolInput` on the entry — add it in Step 1 if absent.)

- [ ] **Step 5: Update the markup** in `ProgressTrail.svelte`:

```svelte
        <Tooltip text={entry.type === 'tool' ? (entry.description ?? entry.toolName ?? '') : ''}>
          <span class="progress-trail-text">
            {#if isFailed}
              <span class="trail-failed-prefix">{$_('progress.failed')}</span>
              <bdi>{entry.description ?? entry.toolName ?? entry.text ?? ''}</bdi>
            {:else if entry.type === 'tool'}
              {@html renderToolText(entry)}
            {:else}
              {entry.text ?? ''}
            {/if}
          </span>
        </Tooltip>
```
(Failed entries show the ref as plain bidi text — no link.)

- [ ] **Step 6: Build and manually verify**

Run: `npm run build`
Expected: success (only pre-existing warnings).
Manual: send a prompt that triggers `get_text`; confirm the trail shows "Fetching text <link>" where the link goes to `sefaria.org/<url_ref>`; switch `interface-lang="he"` and confirm the label is Hebrew and reads correctly (no reversed punctuation).

- [ ] **Step 7: Commit**

```bash
git add src/lib/api.js src/components/LCChatbot.svelte src/components/ProgressTrail.svelte
git commit -m "refactor: render trail refs from backend ref data, drop client parsing"
```

---

### Task 4: Frontend — migrate the location pin to `/api/ref`

**Files:**
- Modify: `src/components/LCChatbot.svelte` (`parseSefariaRef` → async via `/api/ref`; `await` at call sites)
- Modify: `src/components/LocationTag.svelte` (bidi-isolate the label)

**Interfaces:**
- Consumes: `https://www.sefaria.org/api/ref/<tref>`.
- Produces: `async parseSefariaRef(href) -> { label, url } | null`.

- [ ] **Step 1: Rewrite `parseSefariaRef` as async** (multi-line, no nested ifs):

```js
  async function parseSefariaRef(href) {
    const tref = extractCandidateTref(href);
    if (!tref) {
      return null;
    }
    const refData = await fetchRefData(tref);
    if (!refData || !refData.is_ref) {
      return null;
    }
    const label = (interfaceLang === 'he' && refData.he) ? refData.he : refData.en;
    return { label, url: `https://www.sefaria.org/${refData.url_ref}` };
  }

  function extractCandidateTref(href) {
    let url;
    try {
      url = new URL(href);
    } catch {
      return null;
    }
    if (!isSefariaHostname(url.hostname)) {
      return null;
    }
    const path = decodeURIComponent(url.pathname).replace(/^\//, '');
    const skip = /^(topics|sheets|search|profile|collections|groups|community|static|api|questions|calendars|donate|account|login|register)\//i;
    if (!path || skip.test(path)) {
      return null;
    }
    return path;
  }

  async function fetchRefData(tref) {
    try {
      const res = await fetch(`https://www.sefaria.org/api/ref/${encodeURIComponent(tref)}`);
      if (!res.ok) {
        return null;
      }
      return await res.json();
    } catch {
      return null;
    }
  }
```
(`interfaceLang` is the component's existing interface-language value — confirm the variable name with `grep -n "interfaceLang\|interface-lang\|interface_lang" src/components/LCChatbot.svelte`; use the locale store if there's no local var.)

- [ ] **Step 2: Await the call sites** — find every `parseSefariaRef(` (e.g. in `handleSend`): `grep -n "parseSefariaRef(" src/components/LCChatbot.svelte`. Each caller is already inside an `async` function; change to `const locationRef = await parseSefariaRef(window.location.href);`.

- [ ] **Step 3: Bidi-isolate the LocationTag label** — in `LocationTag.svelte`, wrap the rendered `label` in `<bdi>{label}</bdi>` so a Hebrew ref renders correctly next to the pin icon. Ensure the tag uses logical properties (no physical `left/right`).

- [ ] **Step 4: Build and manually verify**

Run: `npm run build`
Expected: success.
Manual: on a Sefaria reader page (e.g. `/Genesis.1.1`), confirm the pin shows the ref and links correctly; on a non-text page confirm no pin; in HE interface confirm the Hebrew ref label.

- [ ] **Step 5: Commit**

```bash
git add src/components/LCChatbot.svelte src/components/LocationTag.svelte
git commit -m "refactor: resolve location pin ref via /api/ref"
```

---

### Task 5: Bilingual CSS verification + conventions sweep of touched code

**Files:**
- Modify (only if needed): `src/components/ProgressTrail.svelte`, `src/components/LocationTag.svelte`, `src/components/LCChatbot.svelte` (CSS logical properties / `<bdi>`)
- Review: all files touched in Tasks 1–4 for the Global Constraints

- [ ] **Step 1: Bidi/RTL audit** — confirm every place a ref label can render (trail link, failed ref, location pin) is wrapped in `<bdi>` and that the trail container's forced `direction: ltr` does not reverse Hebrew labels. Fix any physical `left/right` in touched CSS to logical properties.

- [ ] **Step 2: Conventions audit** — grep the diff for one-line function bodies and nested `if`s introduced in Tasks 1–4: `git diff main... -- '*.py' '*.svelte' '*.js' | grep -nE "function .*\{.*\}$|if .*:\s*\S"`. Refactor any to multi-line / early-return form.

- [ ] **Step 3: Full verification**

Run backend: `cd server && source venv/bin/activate && DJANGO_SETTINGS_MODULE=chatbot_server.test_settings python -m pytest chat/V2/agent/ -q`
Run frontend: `npm run build`
Expected: backend green; build succeeds.
Manual: final EN + HE pass over trail links and location pin.

- [ ] **Step 4: Commit (if any fixes)**

```bash
git add -A
git commit -m "style: bidi-safe ref rendering and conventions cleanup"
```

---

## Self-Review

**Spec coverage:**
- Part 1 (backend resolve + enrich) → Tasks 1, 2. ✓
- Part 2 (frontend render, delete parsing) → Task 3. ✓
- Part 3 (location pin /api/ref) → Task 4. ✓
- Part 4 (bilingual CSS / bidi) → Tasks 3, 4, 5. ✓
- Part 5 (conventions, applied + documented) → Tasks 1–5 follow Global Constraints; doc already in wiki `code-conventions`. ✓

**Type consistency:** `resolve_ref` returns `{is_ref, url_ref, en, he}` (Task 1); `resolve_tool_ref` returns the same or None (Task 2); `AgentProgressUpdate.ref_data` (Task 2) → SSE `refData` (Task 2) → entry `refData` (Task 3) → `renderToolText`/`refDisplayLabel` consume `is_ref/url_ref/en/he` (Task 3). Pin's `fetchRefData` returns the raw API JSON (`is_ref/url_ref/normalized/hebrew`) — Task 4 reads `refData.en`?? No: pin reads `refData.he`/`refData.en` — **fix:** the pin consumes the raw API shape, so it must read `refData.hebrew`/`refData.normalized`, not `en`/`he`. See correction below.

**Correction (applied):** In Task 4 Step 1, `fetchRefData` returns the raw `/api/ref` JSON, whose fields are `normalized`/`hebrew` (not `en`/`he`). Use:
```js
    const label = (interfaceLang === 'he' && refData.hebrew) ? refData.hebrew : refData.normalized;
    return { label, url: `https://www.sefaria.org/${refData.url_ref}` };
```

**Placeholder scan:** No TBD/TODO; all code blocks concrete. Two spots require a one-line `grep` confirmation of an existing identifier (the executor's SefariaClient attribute; the interface-language variable) — these are verification steps, not placeholders.
