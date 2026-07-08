# Loading Experience — UX Refinements (round 2)

Refinements from the UX review (Penina Levy). Builds on
`2026-06-15-updated-loading-experience.md`. Branch `feature/updated-loading-experience`.
Each note below is implemented by its own sub-agent (sequential — shared files), which first
deep-extracts its Figma node(s), then implements. Concrete values grounded from code where known.

Global rule confirmed by UX: **All canvas elements render in Hebrew when interface=he, EXCEPT the
thinking steps, which stay in ENGLISH and LEFT-aligned (LTR) always.**

Figma file: `Y31hDgxSjr0l1fcm0nNJSD`. Map tokens → existing `--lc-*` vars; no bare hex.
Chat-bubble max-width = **560px** (`.message-content`). Header tooltip = native `title`, but UX
priority is SPEED → keep the fast CSS `::before` tooltip already in use.

---

## Note 1 — Topics Box  (TopicAppetizer.svelte + i18n)
Figma: 6459:9608, 6457:5403, 6447:8055, 6447:8289
- Topic-link text style is NEW and canonical: **Roboto, SemiBold, 12px, weight 600, ALWAYS underlined.**
  (Resolves the earlier HE-bold-700 note — use 600 + underline in both languages; family Heebo for HE text.)
- Box height = **hug** (auto) — responds to sentence length regardless of topic count/length (1–3 topics).
- Sentence with relevant topics as hyperlinks (existing single-key + `{topics}` interpolation is correct).
- Verify boxed loading variant (#f0f7ff bg, 2px primary inline-start border) + collapsed plain-text variant.

## Note 2 — Location Tag  (LocationTag.svelte + LCChatbot.svelte)
Figma: 6459:9586, 6457:5348, 6457:5359, 6447:7701 (long-ref truncation example)
- Always **4px below** the prompt it refers to (margin-top 4px on the tag wrapper).
- **max-width = 560px** (chat-bubble max-width). At max width, text truncates with ellipsis.
- Hover → tooltip with the FULL ref name (fast CSS tooltip).
- **Touch target = the ENTIRE tag** (icon + text + padding all clickable) — it's an `<a>`, ensure the whole
  pill is the hit area, not just text/icon.

## Note 3 — Thinking Step  (ProgressTrail.svelte + i18n)
Figma: 6459:9742, 6457:4812, 6451:4822
- Steps **fill container**; ref name truncates with ellipsis when too long.
- Tooltip on hover over the ref → full ref name; fast (match header tooltip style, but speed first → CSS tooltip).
- **Failed steps appear in the thought process** with: `Failed:` prefix; ref styled NOT clickable
  (no underline, muted/secondary color per Figma); tooltip STILL shows on hover.
- **CRITICAL: thinking steps stay ENGLISH and LEFT-aligned (LTR) even when interface=he.**
  Force `direction: ltr; text-align: left;` on the trail regardless of `.interface-hebrew`.

## Note 4 — Accordion  (Accordion.svelte)
Figma: 6459:9628
- Verify against node (states collapsed/expanded; title copy topics vs thought; chevron; RTL mirror).
  Mostly implemented — confirm spacing/typography match.

## Note 5 — Loading Experience Part 1  (LCChatbot.svelte + ProgressTrail.svelte)
Figma: 6447:7209, 6447:7474
- Loader animation = **loader-circle** (search Figma for "loader-circle"); apply the CORRECT icon color.
- The thinking message + loader animation now appears **BELOW the topics box** (topics box first, then loader/trail).

## Note 6 — Loading Experience Part 2  (LCChatbot.svelte)
Figma: 6459:9580, 6447:8558
- Thinking messages appear one by one; **Topics Box (if present) stays visible** above them.
- **Auto-scroll keeps the newest message in view** during the thinking process (smooth).

## Note 7 — Final Response  (LCChatbot.svelte)
Figma: 6459:9581
- Verify response package: collapsed topics accordion + collapsed thought accordion + answer; 80px top offset; ordering.

---

## Orchestration
Sequential sub-agents in this order (each deep-extracts its Figma nodes, then implements, builds, commits):
N4 Accordion → N1 Topics → N3 Thinking (incl. EN/LTR) → N2 Location → N5+N6 Loading → N7 Final.
Then a Haiku Playwright sub-agent tests the full flow on localhost:8000 vs Figma.
