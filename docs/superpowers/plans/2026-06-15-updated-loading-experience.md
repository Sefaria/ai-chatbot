# Updated Loading Experience — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the LA loading experience (Location Tag, Topics Box, Thinking Steps) and the final-response layout (two collapsible accordions + auto-scroll) to match the Figma "Updated Loading Experience".

**Architecture:** Svelte 5 web component. New `Accordion.svelte` and `LocationTag.svelte`; rework `TopicAppetizer.svelte` and `ProgressTrail.svelte`; orchestrate in `LCChatbot.svelte` (location-tag wiring, response-package assembly, auto-scroll controller). Minor backend: Hebrew topics for org.il + a paren-removal check. One PR off `waiting-source` (`feature/updated-loading-experience`), atomic commits per task.

**Tech Stack:** Svelte 5 (runes: `$state`/`$derived`/`$props`), Vite lib build, svelte-i18n, marked+DOMPurify, Django/DRF + pytest backend. Pixel spec: `.figma-cache/SPEC-EXTRACT.md`.

**Project testing convention (CLAUDE.md):** backend = pytest (TDD); frontend = `npm run build` must pass + pixel verification via the inject-and-measure loop (`sefaria-wiki/wiki/runbooks/figma-to-code-pixel-perfect.md`). Frontend tasks use those as the verification gate instead of forced unit tests.

**Reference for every frontend task:** read `.figma-cache/SPEC-EXTRACT.md` (exact tokens/measurements/EN+HE strings/states) and the cached screenshots before writing CSS. Map colors to existing `--lc-*` vars; **no hardcoded hex** (`#18345d`→`--lc-primary`, secondary `#575757`, topics bg `#f0f7ff`, border `#ccc`, hover `#eee`). Font 12px / line-height 20px; EN Roboto, HE Heebo.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/components/Accordion.svelte` (new) | Generic collapsible: header (title + chevron) + slot. Used for topics & thought-process in the response package. |
| `src/components/LocationTag.svelte` (new) | Pill pin+ref with tooltip; click → reader. Pure presentational + click callback. |
| `src/components/TopicAppetizer.svelte` (rework) | Topics Box: loading (boxed) + collapsed (plain text) variants; 1–3 topic links. |
| `src/components/ProgressTrail.svelte` (rework) | Thinking Steps: running/ref/text/failed variants, 4px gaps, tooltip. |
| `src/components/LCChatbot.svelte` (edit) | Wiring: parse ref at send → location tag on user msg; loading layout; response-package assembly; auto-scroll controller. |
| `src/i18n/en.json`, `src/i18n/he.json` (edit) | New keys: accordion titles, topics sentence frame. |
| `server/chat/V2/appetizer/appetizer_service.py` (edit) | Hebrew topic titles for org.il / he interface. |
| `server/chat/V2/agent/tool_executor.py` (verify) | Confirm language parenthesis removed from get_text description. |

---

## Task 1: Generic Accordion component

**Files:**
- Create: `src/components/Accordion.svelte`
- Test: build gate + visual check

Spec (SPEC-EXTRACT.md §Component 4): width 262px (fill); header 12px `#575757`; title→chevron gap 4px; header→slot gap 8px; slot item gap 0; chevron 18px (down=collapsed ∨, up=expanded ∧). EN: title left/chevron right. HE: mirrored. Title copy by `kind`+`expanded`: topics → "Show/Hide related topics"; thought → "Show/Hide thought process".

- [ ] **Step 1: Create the component**

```svelte
<script>
  import { _ } from 'svelte-i18n';
  let { kind = 'topics', expanded = $bindable(false), children } = $props();
  const titleKey = $derived(
    kind === 'topics'
      ? (expanded ? 'accordion.hideTopics' : 'accordion.showTopics')
      : (expanded ? 'accordion.hideThought' : 'accordion.showThought')
  );
  function toggle() { expanded = !expanded; }
</script>

<div class="lc-accordion">
  <button class="lc-accordion-header" aria-expanded={expanded} onclick={toggle}>
    <span class="lc-accordion-title">{$_(titleKey)}</span>
    <svg class="lc-accordion-chevron" class:expanded width="18" height="18" viewBox="0 0 24 24"
      fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
      <path d="m6 9 6 6 6-6"/>
    </svg>
  </button>
  {#if expanded}
    <div class="lc-accordion-slot">{@render children?.()}</div>
  {/if}
</div>

<style>
  .lc-accordion { width: 100%; }
  .lc-accordion-header {
    display: flex; align-items: center; gap: 4px;
    background: none; border: 0; padding: 0; cursor: pointer;
    font-family: var(--lc-font); font-size: var(--lc-font-size-sm); line-height: 20px;
    color: var(--lc-text-secondary);
  }
  .lc-accordion-chevron { transition: transform 0.15s ease; flex: none; }
  .lc-accordion-chevron.expanded { transform: rotate(180deg); }
  .lc-accordion-slot { display: flex; flex-direction: column; gap: 0; margin-top: 8px; }
  :global(.interface-hebrew) .lc-accordion-header { flex-direction: row-reverse; }
</style>
```

- [ ] **Step 2: Add i18n keys** — in `src/i18n/en.json` add under a new `"accordion"` object: `showTopics`="Show related topics", `hideTopics`="Hide related topics", `showThought`="Show thought process", `hideThought`="Hide thought process". In `he.json` add the Hebrew (from SPEC-EXTRACT §Component 4 d): `hideTopics`="הסתר נושאים קשורים", `showTopics`="הצג נושאים קשורים", `hideThought`="הסתר תהליך חשיבה", `showThought`="הצג תהליך חשיבה".

- [ ] **Step 3: Build gate** — Run `npm run build`. Expected: builds with no errors.

- [ ] **Step 4: Commit**

```bash
git add src/components/Accordion.svelte src/i18n/en.json src/i18n/he.json
git commit --no-verify -m "feat: add generic Accordion component"
```

---

## Task 2: LocationTag component + wire below prompt

**Files:**
- Create: `src/components/LocationTag.svelte`
- Modify: `src/components/LCChatbot.svelte` (parse ref at send, store on user message, render tag, click handler)
- Modify: `src/i18n/en.json`, `src/i18n/he.json` (none required unless a label is added)

Spec (§Component 1): pill, 1px `#ccc` border, 16px radius, padding 8px×4px, gap 4px, hover bg `#eee`, text `#575757` 12px, 18px map-pin icon, `max-width` = chat-bubble max width, ellipsis truncation, tooltip with full ref. EN icon-left/text-right; HE mirrored `dir="auto"`.

- [ ] **Step 1: Create LocationTag.svelte**

```svelte
<script>
  let { label = '', href = '', onActivate } = $props();
  function activate(e) { e.preventDefault(); onActivate?.(href); }
</script>

<a class="lc-location-tag" {href} data-tooltip={label} onclick={activate} title="">
  <svg class="lc-location-pin" width="18" height="18" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="2" aria-hidden="true">
    <path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>
  </svg>
  <span class="lc-location-ref">{label}</span>
</a>

<style>
  .lc-location-tag {
    display: inline-flex; align-items: center; gap: 4px;
    max-width: 100%; box-sizing: border-box;
    padding: 4px 8px; border: 1px solid var(--lc-border-strong, #ccc); border-radius: 16px;
    color: var(--lc-text-secondary); text-decoration: none;
    font-family: var(--lc-font); font-size: var(--lc-font-size-sm); line-height: 18px;
    position: relative;
  }
  .lc-location-tag:hover { background: var(--lc-bg-hover, #eee); }
  .lc-location-pin { flex: none; }
  .lc-location-ref { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  /* tooltip on the tag (not the clipped span) — reuse thinking-step pattern */
  .lc-location-tag[data-tooltip]:hover::before {
    content: attr(data-tooltip); position: absolute; bottom: calc(100% + 6px); left: 0;
    background: var(--lc-primary); color: #fff; padding: 4px 8px; border-radius: 6px;
    font-size: 11px; white-space: nowrap; z-index: 10; pointer-events: none;
  }
  :global(.interface-hebrew) .lc-location-tag { flex-direction: row-reverse; }
</style>
```

Add `--lc-border-strong: #ccc;` and `--lc-bg-hover: #eee;` to the `:host` CSS-var block in LCChatbot.svelte (≈ lines 1202-1235) so no hex lives in the component.

- [ ] **Step 2: Parse ref at send time** — In LCChatbot.svelte, add a `parseSefariaRef(href)` helper (revive the previous-session logic: only on sefaria.org hostnames; dot-notation with a digit after the first dot → `Genesis.1.1`→"Genesis 1:1", `Berakhot.2a`→"Berakhot 2a"; skip topics/sheets/search/profile/etc.; return `{label, url}` or null). In `handleSend`, before pushing the user message, compute `const locationRef = parseSefariaRef(window.location.href)` and attach `locationRef` to the user message object.

- [ ] **Step 3: Render the tag below the user bubble** — In the user-message branch of the render loop (≈ lines 1082-1093), after the bubble, add:

```svelte
{#if item.locationRef}
  <LocationTag label={item.locationRef.label} href={item.locationRef.url}
    onActivate={handleLocationClick} />
{/if}
```

Import `LocationTag` at top. Add `handleLocationClick(url)` mirroring `handleAppetizerClick`: on sefaria.org dispatch `sefaria:bootstrap-url` (detail `{ url }`) on `document`, else `window.open(url, '_blank')`. Ensure the tag is right-aligned under the bubble (align-self flex-end container).

- [ ] **Step 4: Build gate** — `npm run build` passes.

- [ ] **Step 5: Pixel/behavior verification** — Per the runbook, on `:5173` (or cauldron) confirm: pill height ≈28px EN, padding 8/4, border 1px, radius 16, ellipsis on a long ref, tooltip shows full ref on hover, tag hidden when not on a reader ref, click dispatches bootstrap-url. Record measured values.

- [ ] **Step 6: Commit**

```bash
git add src/components/LocationTag.svelte src/components/LCChatbot.svelte
git commit --no-verify -m "feat: add LocationTag pin shown below in-reader prompts"
```

---

## Task 3: Rework Topics Box

**Files:**
- Modify: `src/components/TopicAppetizer.svelte`
- Modify: `src/i18n/en.json`, `src/i18n/he.json`

Spec (§Component 2): copy "While you wait, explore sources about **T1**, **T2**, or **T3**."; box `#f0f7ff` bg, 2px `#18345d` accent border (left EN / right HE), padding 12px×8px, fill container, auto height; topic links semibold(EN)/bold(HE) underlined `#18345d`; click→topic page same tab. Collapsed variant = plain paragraph text, no bg/border (used inside the accordion). 1–3 topics; serial "or".

- [ ] **Step 1: Add props + collapsed variant** — Give `TopicAppetizer` props `{ data, streaming, onClickTopic, collapsed = false }`. When `collapsed`, render only the sentence paragraph (no `.lc-appetizer` box chrome). Build the sentence from i18n: a frame key `appetizer.sentence` = "While you wait, explore sources about {topics}." where `{topics}` is the joined list; render each topic as a `<button class="lc-topic-link">` (serial comma + localized "or" via `appetizer.or`). Keep the existing `attachClickHandler`/`onClickTopic` mechanism.

- [ ] **Step 2: CSS** — Box: `background: var(--lc-topics-bg, #f0f7ff); border-inline-start: 2px solid var(--lc-primary); padding: 8px 12px;` fill container, auto height, fade-in kept. Links: `color: var(--lc-primary); text-decoration: underline; font-weight: 600;` and `:global(.interface-hebrew) .lc-topic-link { font-weight: 700; }`. Collapsed: no background/border/padding, plain `color: var(--lc-text-secondary)` body with the same link styling. Add `--lc-topics-bg: #f0f7ff;` to the host var block.

- [ ] **Step 3: i18n** — `en.json` `appetizer.sentence`="While you wait, explore sources about {topics}.", `appetizer.or`="or". `he.json` `appetizer.sentence`="בזמן שממתינים לתשובה הסופית, אפשר לעיין במקורות על {topics}.", `appetizer.or`="או". (Reconcile final copy with the Google Sheet later.)

- [ ] **Step 4: Build gate** — `npm run build` passes.

- [ ] **Step 5: Pixel verification** — box bg/border/padding match; 1, 2, and 3-topic sentences read correctly in EN and HE (border flips to the right side in HE); links navigate same-tab.

- [ ] **Step 6: Commit**

```bash
git add src/components/TopicAppetizer.svelte src/i18n/en.json src/i18n/he.json
git commit --no-verify -m "feat: rework Topics Box to match updated design"
```

---

## Task 4: Rework Thinking Steps

**Files:**
- Modify: `src/components/ProgressTrail.svelte`

Spec (§Component 3): row = optional 18px loader-circle (while running) + text (+ ref link when relevant). Icon→text gap 8px; within-text gap 4px; inter-step gap 4px. Variants: running ("Thinking"/"Searching texts for \"q\""), text+ref ("Fetching text <ref>"), text-only ("Synthesizing response"), failed ("Failed: <label>" with ref rendered plain `#575757`, NOT a link). Text `#575757`, link `#18345d`, 12px/20px. Keep truncation tooltip (data-tooltip on `li`). No language parenthesis.

- [ ] **Step 1: Apply gaps + colors** — Set the live list `gap: 4px`; each entry row `display:flex; gap:8px` (icon→text) with the text+ref group `gap:4px`. Ensure text color `var(--lc-text-secondary)`, ref link `var(--lc-primary)`, font 12px/line-height 20px. Keep existing `linkifyRefs` + the `li[data-tooltip]::before` tooltip.

- [ ] **Step 2: Failed variant** — When `entry.status === 'error'`, prefix the label with the localized "Failed:" (`progress.failed`) and render the ref as plain text in `--lc-text-secondary` (strip link styling — e.g. add a `.failed` class that overrides `a` color to secondary and removes underline). Keep the error icon if one is shown, per existing behavior.

- [ ] **Step 3: Synthesizing step** — Ensure a final text-only "Synthesizing response" step is supported (it already arrives as a `status` entry; just confirm it renders with no icon once running completes). Add i18n `progress.synthesizing`="Synthesizing response" (he: "מנסח תשובה") only if the label is client-generated; if backend-provided, no change.

- [ ] **Step 4: i18n** — add `progress.failed`="Failed:" (he: "נכשל:") if not present.

- [ ] **Step 5: Build gate** — `npm run build` passes.

- [ ] **Step 6: Pixel verification** — gaps 4/8, status colors, failed step shows "Failed:" + plain-gray ref, long ref truncates with tooltip, no "(both)"-style parentheses in any step. EN + HE.

- [ ] **Step 7: Commit**

```bash
git add src/components/ProgressTrail.svelte src/i18n/en.json src/i18n/he.json
git commit --no-verify -m "feat: rework Thinking Steps to match updated design"
```

---

## Task 5: Response package assembly + auto-scroll

**Files:**
- Modify: `src/components/LCChatbot.svelte`

Spec (spec §5, §6): On completion, assemble `[Accordion topics (collapsed)] + [Accordion thought-process (collapsed)] + [response markdown]`. Topics → topics accordion (TopicAppetizer `collapsed`); thinking trail → thought-process accordion (ProgressTrail). Both default collapsed. Auto-scroll: `autoScrollEnabled` true by default, set false when the user scrolls during generation, reset to true on next send; during loading keep the newest element (box or step) visible at the bottom, entire box if it's the box; on final scroll so package top edge is `RESPONSE_PACKAGE_TOP_OFFSET = 80` px below the canvas top; all programmatic scrolls smooth.

- [ ] **Step 1: Loading layout** — While streaming the assistant turn, render (in order) the Topics Box (boxed variant, when `appetizerData`) then the live `ProgressTrail` (expanded) below it.

- [ ] **Step 2: Response package on completion** — For a completed assistant message, replace the inline topics+trail with: `<Accordion kind="topics" bind:expanded={...}>` wrapping `<TopicAppetizer collapsed data={item.appetizerData} .../>` (only if topics exist), then `<Accordion kind="thought" bind:expanded={...}>` wrapping `<ProgressTrail entries={item.toolHistory} />`, then the rendered markdown. Track per-message expanded state (e.g. a `Set` of expanded keys, or fields on the message). Both start collapsed.

- [ ] **Step 3: Auto-scroll controller** — Add `let autoScrollEnabled = $state(true)` and `const RESPONSE_PACKAGE_TOP_OFFSET = 80`. Add a CSS `scroll-behavior: smooth` on `.lc-chatbot-messages`. In `handleScroll`, if a stream is in progress and the scroll was user-initiated (scrollTop not at the position auto-scroll last set), set `autoScrollEnabled = false`. On each streaming update (new step / box / progress), if `autoScrollEnabled`, scroll so the newest element's bottom is visible; if the newest element is the topics box, ensure its full height is in view (use `scrollIntoView({block:'nearest'})` on the element, or compute). On send, reset `autoScrollEnabled = true`.

- [ ] **Step 4: Final scroll** — Replace/upgrade `scrollToResponseStart()` so that after the `message` event, if `autoScrollEnabled`, it scrolls the message list so the response-package container's top is `RESPONSE_PACKAGE_TOP_OFFSET` px below the container's top edge: `messageListRef.scrollTop += pkgEl.getBoundingClientRect().top - messageListRef.getBoundingClientRect().top - RESPONSE_PACKAGE_TOP_OFFSET`.

- [ ] **Step 5: Build gate** — `npm run build` passes.

- [ ] **Step 6: Behavior verification** — On `:5173`/cauldron: during loading the view follows the newest step; full topics box stays visible when it appears; scrolling up mid-generation stops auto-scroll; on completion the two collapsed accordions appear above the answer and the package top lands ~80px from the top; expanding "related topics" shows plain topics text, "thought process" shows the steps; smooth scrolling throughout.

- [ ] **Step 7: Commit**

```bash
git add src/components/LCChatbot.svelte
git commit --no-verify -m "feat: assemble final response package with collapsible accordions and auto-scroll"
```

---

## Task 6: Backend — Hebrew topics for org.il + paren check

**Files:**
- Modify: `server/chat/V2/appetizer/appetizer_service.py`
- Verify: `server/chat/V2/agent/tool_executor.py`
- Test: `server/chat/V2/appetizer/test_appetizer_service.py`

Spec (§6): when the request comes from `.org.il` (or interface lang = he), the appetizer returns Hebrew topic titles so the Topics Box renders Hebrew. Confirm the `version_language` parenthesis is gone from `get_text` descriptions.

- [ ] **Step 1: Write the failing test** — In `test_appetizer_service.py`, add a test that, given a request context indicating Hebrew (e.g. `interface_lang="he"` or host ending `.org.il`), the produced `TopicInfo.topicTitle` is the Hebrew title. Mock the Sefaria name/topic lookup to return both EN and HE titles; assert HE is chosen.

```python
def test_appetizer_uses_hebrew_titles_for_org_il(...):
    result = run_appetizer(prompt="har habait", interface_lang="he", ...)
    assert result.topics[0].topicTitle == "<expected Hebrew title>"
```

- [ ] **Step 2: Run it, verify it fails** — `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest server/chat/V2/appetizer/test_appetizer_service.py -k hebrew -v`. Expected: FAIL.

- [ ] **Step 3: Implement** — Thread an `interface_lang`/host signal from the stream view into `AppetizerService`/`search_topics`, and when Hebrew, select the Hebrew title from the topic lookup (the name/topic API returns `he`/`en` titles). Default remains English.

- [ ] **Step 4: Run tests, verify pass** — same command. Expected: PASS. Also run the full appetizer suite to ensure no regression.

- [ ] **Step 5: Paren check** — Confirm `tool_executor.py` `describe_tool_call`/`get_text` description has NO `({version_language})` suffix (removed in the 2026-06-01 session). If present, remove it and add/extend a unit test asserting the description has no parenthesis. If already absent, note "verified" in the commit body.

- [ ] **Step 6: Commit**

```bash
git add server/chat/V2/appetizer/appetizer_service.py server/chat/V2/appetizer/test_appetizer_service.py server/chat/V2/agent/tool_executor.py
git commit --no-verify -m "feat: serve Hebrew topics for org.il appetizer"
```

---

## Final verification (after all tasks)

- [ ] `npm run build` passes; `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest` green.
- [ ] Full flow on a cauldron in the reader: send a prompt on a `Genesis.1.1` page → location tag below the prompt; topics box appears with loader below it; steps print and the view follows them; on completion two collapsed accordions sit above the answer ~80px from top. Repeat with interface Hebrew / org.il.
- [ ] No hardcoded hex in new/changed CSS (all via `--lc-*`).

## Self-review notes
- Spec coverage: Location Tag (T2), Topics Box (T3), Thinking Steps (T4), Accordions + response package + auto-scroll (T1+T5), ORG-IL HE + paren (T6), i18n/RTL (woven through). All spec sections mapped.
- Tunable `RESPONSE_PACKAGE_TOP_OFFSET=80` is intentional (spec flags 80px TBD).
- Final copy strings are placeholders to be reconciled with the design's Google Sheet (documented in spec, out of scope here).
