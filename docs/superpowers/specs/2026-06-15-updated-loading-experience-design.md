# Updated Loading Experience — Design Spec

**Date:** 2026-06-15
**Branch:** `feature/updated-loading-experience` (off `waiting-source`)
**Figma:** [Library Assistant Wireframes — "↳Updated Loading Experience"](https://www.figma.com/design/Y31hDgxSjr0l1fcm0nNJSD/Library-Assistant-Wireframes?node-id=6345-2149)
**Pixel spec (tokens/measurements/annotations, verbatim):** `.figma-cache/SPEC-EXTRACT.md` + cached screenshots in `.figma-cache/`

## Goal

Redesign the LA "loading experience" (the moment between sending a prompt and reading the
answer) and the final-response layout to match the Figma "Updated Loading Experience". Ships
as ONE PR off `waiting-source` with atomic per-component commits.

## Components (units)

| # | Unit | File | Type |
|---|------|------|------|
| 1 | Accordion (generic) | `src/components/Accordion.svelte` | new |
| 2 | Location Tag (pin) | `src/components/LocationTag.svelte` | new |
| 3 | Topics Box | `src/components/TopicAppetizer.svelte` | rework |
| 4 | Thinking Steps | `src/components/ProgressTrail.svelte` | rework |
| 5 | Orchestration | `src/components/LCChatbot.svelte` | edit |
| 6 | Backend: ORG-IL HE topics + paren check | `server/chat/V2/appetizer/appetizer_service.py`, `server/chat/V2/agent/tool_executor.py` | edit |

All colors map to existing `--lc-*` CSS vars / `color-palette.css`; **no hardcoded hex**. Tokens:
primary/link `#18345d` → `--lc-primary`; secondary text `#575757`; topics bg `#f0f7ff`; gray
border `#ccc`, hover `#eee`. Font 12px, line-height 20px (EN Roboto / HE Heebo). See SPEC-EXTRACT.md.

---

### 1. Accordion.svelte (new, generic)
- Props: `title` (collapsed/expanded label pair OR a `kind: 'topics'|'thought'` + `expanded` to derive label), `expanded` (bindable), `onToggle`, slotted content.
- Header: title text + chevron (down=collapsed ∨, up=expanded ∧). EN: text left/chevron right; HE: mirrored. Gap title→chevron 4px; header→slot 8px; slot item gap 0.
- Title copy by content+state: topics → "Show/Hide related topics"; thinking → "Show/Hide thought process". i18n keys.
- Width 262px (fills container). Header color `#575757`, 12px.
- Used twice in the response package (topics, thought process).

### 2. LocationTag.svelte (new)
- Pill: pin icon (18px map-pin) + ref name. 1px `#ccc` border, 16px radius, padding 8px×4px, gap 4px. Hover bg `#eee`. Text `#575757` 12px. `max-width` = chat-bubble max width; ellipsis truncation.
- **Tooltip** (required by non-visual spec): full ref via `data-tooltip` + CSS `::before` on the wrapper (reuse the thinking-step tooltip pattern — put it on a non-`overflow:hidden` parent).
- EN: icon left/text right. HE: text right/icon left, `dir="auto"`.
- Behavior: captured at SEND time — when a prompt is submitted while in the reader, parse the ref from `window.location` (revive `parseSefariaRef`, sefaria.org-only; skip topics/sheets/search/etc.) and attach `locationRef = {label, url}` to that user message. Renders below the prompt bubble; persists after navigation. Click → dispatch `sefaria:bootstrap-url` on sefaria.org else open new tab. Shows for ANY prompt type, only when a reader ref exists.

### 3. TopicAppetizer.svelte (rework → Topics Box)
- Copy: "While you wait, explore sources about **T1**, **T2**, or **T3**." (1–3 topics; serial comma + "or"). Topic links semibold (EN) / bold (HE), underlined, `#18345d`.
- Box (loading state): `#f0f7ff` bg, 2px `#18345d` accent border (left EN / right HE), padding 12px×8px, fill container, auto height.
- **Collapsed-into-accordion state**: plain paragraph text only (no bg/border), per Figma `TopicsCollapsed`. The Accordion wraps this variant in the final response.
- Click topic → topic page, same tab (`sefaria:bootstrap-url` on sefaria, else new tab).
- HE/ORG-IL: Hebrew sentence + Hebrew topic titles (titles from backend).

### 4. ProgressTrail.svelte (rework → Thinking Steps)
- Step row: optional animated loader-circle (18px, while running) + text (+ clickable ref link when relevant). Icon→text gap 8px; within-text gap 4px. Inter-step gap 4px (during live trail).
- Step variants: running ("Thinking", "Searching texts for \"<q>\""), text+ref ("Fetching text <ref>"), text-only ("Synthesizing response"), **failed** ("Failed: <label>" — ref rendered plain `#575757`, NOT a link).
- Truncated ref link keeps existing tooltip (data-tooltip on `li`).
- Drop the language parenthesis (e.g. "(both)") — verify backend `tool_executor.py` already removed it; ensure no client-side reintroduction.
- Text `#575757`, link `#18345d`, 12px/20px.

### 5. LCChatbot.svelte (orchestration)
- **Location tag wiring**: parse ref at send, store on the user message, render `<LocationTag>` below the user bubble.
- **Loading layout**: while streaming, render Topics Box (when present) then the live Thinking Steps below it, inside the assistant turn.
- **Response package** (on completion): assemble `[Accordion topics (collapsed)] + [Accordion thought-process (collapsed)] + [response markdown]`. Topics box → topics accordion (plain-text variant); thinking trail → thought-process accordion. Both default collapsed.
- **Auto-scroll controller**:
  - `autoScrollEnabled` (default true). A user scroll during generation sets it false (re-enable on next send).
  - During loading: keep the latest element (box or newest step) visible at the bottom; if it's the topics box, keep the ENTIRE box in view.
  - On final response: scroll so the response-package top edge sits `RESPONSE_PACKAGE_TOP_OFFSET = 80px` (named constant, tunable) below the canvas top.
  - All programmatic scrolls use `scroll-behavior: smooth`.
  - Upgrade existing `scrollToBottom()` / `scrollToResponseStart()` rather than adding parallel logic.

### 6. Backend
- **ORG-IL HE topics** (`appetizer_service.py`): when the request originates from `.org.il` (or interface lang = he), request Hebrew topic titles so the Topics Box renders Hebrew. (Fewer HE topics exist — acceptable per spec.) Plumb host/lang from the stream request.
- **Paren check** (`tool_executor.py`): confirm the `version_language` parenthesis was removed from `get_text` descriptions; restore the removal if missing.

## i18n / RTL
New strings in `en.json` / `he.json` via `svelte-i18n` `$_()`. Final copy reconciled against the
design's Google Sheet later. RTL via existing interface-lang flow; mirror Location Tag, Topics Box,
Accordion, Thinking Steps for HE.

## Out of scope
- The hover popover that lists multiple full refs (Figma "hover/pressed" callout) — not specified
  as a component; the non-visual spec's tooltip (single full ref) covers the requirement.
- Final copy wording (comes from the Google Sheet); we ship sensible EN/HE keys now.

## Testing / verification
- `npm run build` must pass. Add/adjust frontend checks where practical; `pytest` for backend changes.
- Pixel verification per `sefaria-wiki/wiki/runbooks/figma-to-code-pixel-perfect.md`: inject-and-measure
  on local `:5173` first, cauldron for final EN + HE + in-reader (location tag) validation before merge.

## Commit sequence (one PR)
1. `feat: add generic Accordion component`
2. `feat: add LocationTag (pin) component + wire below prompt`
3. `feat: rework Topics Box to match updated design`
4. `feat: rework Thinking Steps to match updated design`
5. `feat: assemble final response package + auto-scroll rules`
6. `feat: serve Hebrew topics for org.il appetizer` (+ paren check)
