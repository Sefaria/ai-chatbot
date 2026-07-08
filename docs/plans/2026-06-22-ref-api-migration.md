# Ref API Migration + Bilingual CSS + Code Conventions

**Date:** 2026-06-22
**Status:** Design approved, pending spec review
**Origin:** PR #154 review (Yishai) — refAPI notes; Sefaria-Project PR #3191 (`GET /api/ref/<tref>`)

## Goal

Replace fragile client-side Sefaria ref parsing with the canonical `/api/ref/<tref>` endpoint, make ref rendering bilingual (HE/EN) and RTL-safe, and apply two coding conventions to all code touched.

The endpoint (live, sefaria.org) returns for a `tref`:
`{ is_ref, normalized (EN), hebrew (HE), url_ref, index_title, node_type, … }`, and `{ is_ref: false }` for invalid input.

## Scope (decided)

1. **Thinking-trail refs** → resolved on the **backend**, attached to progress events (backend enrichment).
2. **Location pin** (`parseSefariaRef`) → resolved on the **frontend** via `/api/ref`.
3. **CSS** → correct bidi for Hebrew ref labels inside the LTR trail; logical properties throughout.
4. **Conventions** (applied to all touched code, documented in the engineering wiki `code-conventions` page):
   - No one-line function bodies — always multi-line.
   - No nested `if`s — use early returns / guard clauses / combined conditions.

Out of scope (tracked, not done): localizing the trail *verbs* ("Fetching text") — only the ref label is bilingual for now; migrating other client-side ref utilities beyond those listed.

---

## Part 1 — Backend: resolve refs, enrich progress events

### 1a. `SefariaClient.resolve_ref(tref)` — `server/chat/V2/agent/sefaria_client.py`
- `GET {base_url}/api/ref/<url-encoded tref>` via the existing pooled `httpx.AsyncClient`.
- Return a slim dict `{ "is_ref", "url_ref", "en", "he" }` (mapping `normalized`→`en`, `hebrew`→`he`), or `None` when `is_ref` is false, on HTTP error, or on timeout.
- **Cache**: per-process dict keyed by `tref` (same refs recur within and across turns). Bounded (e.g. simple dict with a size cap, or `functools`-style LRU around an async wrapper).
- Short, defensive: failures never raise into the stream.

### 1b. Ref-bearing tool map — `server/chat/V2/agent/tool_executor.py` (near `describe_tool_call`)
```
REF_TOOL_ARG = {
    "get_text": "reference",
    "get_links_between_texts": "reference",
    "get_available_manuscripts": "reference",
    "get_english_translations": "reference",
}
```
Only these tools carry a ref; searches carry queries (no ref).

### 1c. Progress event contract — `server/chat/V2/agent/contracts.py`
Add to `AgentProgressUpdate`:
```
ref_data: dict | None = None   # { is_ref, url_ref, en, he } for ref-bearing tools
```

### 1d. Emit enrichment — `server/chat/V2/agent/tool_runtime.py`
In `build_handler`, when the tool is in `REF_TOOL_ARG`:
- resolve the arg value via `resolve_ref`,
- attach the result as `ref_data` on the **`tool_start`** `AgentProgressUpdate`.
- Resolution is cached + same-infra; on failure `ref_data` is omitted (frontend shows plain text).
- *Perf fallback (only if this delays the line noticeably):* emit `tool_start` immediately and attach `ref_data` to `tool_end` instead.

### 1e. SSE serialization — `server/chat/V2/views.py`
Include `ref_data` in the JSON written for `tool_start` events.

### 1f. Backend tests
- `resolve_ref`: valid ref → slim dict; `is_ref:false` → None; HTTP error/timeout → None; cache hit avoids a second call (assert call count).
- Enrichment: a `get_text` tool_start event carries `ref_data`; a `text_search` event does not.

---

## Part 2 — Frontend: render provided ref data, delete client parsing

### 2a. SSE parsing — `src/lib/api.js`
Pass `ref_data` through from the `tool_start` event into the `onProgress` payload.

### 2b. `src/components/LCChatbot.svelte`
- `onProgress` `tool_start` branch: store `ref_data` on the `toolHistory` entry (`{ …, refData }`).
- `finalTrail` persists `refData` so links survive reload.
- **`parseSefariaRef`** (location pin) — see Part 3.

### 2c. `src/components/ProgressTrail.svelte`
- **Delete** `refToUrl`, `refLabel`, `substituteRefs`, `linkifyRefs`, `plainRefs`, `SEFARIA_BASE_URL`.
- New render: given an entry with `description` text and optional `refData`:
  - If `refData?.is_ref`: render the description with the ref token replaced by a link — `href = sefaria base + refData.url_ref`, label = **interface-language** (`refData.he` when HE UI, else `refData.en`). The ref token to replace is the entry's known ref string (`tool_input.reference`), located in the description; this is a single substring swap of a *known* value, not parsing/validation.
  - Else (no `refData` / `is_ref:false` / failed entry): plain text, no link.
- HTML-escape the non-link text; only the `<a>`/`<bdi>` is markup.

---

## Part 3 — Location pin: frontend `/api/ref`

`src/components/LCChatbot.svelte` `parseSefariaRef(href)` becomes async:
- Still extract a candidate `tref` from `window.location` (strip locale prefix, decode path).
- Call `GET https://www.sefaria.org/api/ref/<tref>` (absolute; same-origin when embedded on sefaria.org).
- Use `is_ref` to decide whether to show the pin; `url_ref` for the link; label per interface language (`hebrew`/`normalized`).
- Async: no pin until resolved; no pin on `is_ref:false`, error, or timeout.
- **CORS fallback (only if off-site embeds are blocked):** add a thin chatbot-backend proxy endpoint `/api/v2/ref?tref=` that calls `SefariaClient.resolve_ref`. Not built unless needed.

---

## Part 4 — CSS: Hebrew + English

- Hebrew ref labels appear inside the otherwise-LTR trail and pin. Wrap each rendered ref label in `<bdi>` (or `dir="auto"`) so RTL text renders correctly and does not reorder surrounding punctuation/numbers.
- Keep using CSS logical properties (`text-align: start/end`, `margin-inline-*`, `inset-inline-*`); no physical `left/right`.
- Verify the location pin and trail in both `interface-lang="en"` and `interface-lang="he"`.

---

## Part 5 — Conventions (applied to all code touched here)

- No one-line function bodies; always multi-line.
- No nested `if`s — early returns / guard clauses / combined conditions.
- Already recorded in the engineering wiki `repos/ai-chatbot/code-conventions` page (also notes "prefer the ref API over hand-rolled parsing" — this work realizes that).

---

## Risks / considerations

- **Latency**: a `resolve_ref` call before `tool_start` adds one cached, same-infra GET per ref-bearing tool. Mitigated by caching; `tool_end` fallback documented if needed.
- **`SEFARIA_API_BASE_URL`**: backend uses a configurable base (defaults to sefaria.org / a cauldron). `resolve_ref` must use the production-equivalent base that actually serves `/api/ref`.
- **Label/verb language mismatch**: trail verbs stay English this round; a HE ref label inside an English verb line is acceptable and bidi-isolated. Full verb localization is a separate effort.
- **Persistence**: `refData` is persisted in `toolHistory`; old stored messages without `refData` simply render plain text (graceful).

## Test strategy

- Backend: unit tests for `resolve_ref` (valid/invalid/error/cache) and enrichment (ref vs non-ref tool).
- Frontend: `npm run build`; manual verify in EN and HE — trail links resolve to correct URLs and labels; failed steps show plain refs; location pin appears only for valid refs and is bilingual; bidi correct.
