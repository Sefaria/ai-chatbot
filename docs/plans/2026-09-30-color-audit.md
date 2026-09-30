# Color audit — 2026-09-30

Inventory of every color token and hard-coded color in the shipped widget (`src/`, `public/static/icons/`).
The `index.html` demo page is out of scope. "Figma" means the Foundations library, with values read from
the Library-Assistant-Wireframes file.

✓ = value matches a Figma token · ✗ = no Figma token has this value · — = not checked

## 1. Tokens defined in `:host` (`src/components/LCChatbot.svelte` ~L2334–2390)

### Named after Figma tokens

| Variable | Value | Figma | Uses |
|---|---|---|---|
| `--semantic-action-primary` | `#18345D` | ✓ Semantic/Action/Primary | via `--lc-primary` |
| `--semantic-text-link` | `#18345D` | ✓ Semantic/Text/Link | yes |
| `--semantic-text-secondary` | `#575757` | ✓ Semantic/Text/Secondary | yes |
| `--semantic-text-muted` | `#707070` | ✓ Semantic/Text/Muted | **0 — unused** |
| `--semantic-icon-default` | `#121212` | ✓ Semantic/Icon/Default | yes |
| `--semantic-icon-muted` | `#6f6f6f` | ✓ Semantic/Icon/Muted | yes |
| `--semantic-icon-disabled` | `#999999` | ✓ Semantic/Icon/Disabled | only as a filter (`--lc-icon-disabled-filter`) |
| `--core-blue-tbr-100` | `#F0F7FF` | ✓ Core/blue TBR/100 | yes (TopicAppetizer) |
| `--core-base-white` | `#FFFFFF` | ✓ Core/Base/White | yes |
| `--core-neutral-gray-100` | `#EEEEEE` | — no Gray/100 found (`#eeeeee` = Semantic/Surface/Hover) | via `--lc-bg-hover` |
| `--core-neutral-gray-300` | `#CCCCCC` | ✓ Core/Neutral/Gray/300 | yes |
| `--brand-sefaria-blue` | `#18345D` | ✓ brand/sefaria-blue | yes |

### Component tokens (`--lc-*`)

| Variable | Value | Figma | Note |
|---|---|---|---|
| `--lc-primary` | → `--semantic-action-primary` | ✓ | |
| `--lc-primary-hover` | `#465D7D` | ✗ | |
| `--lc-bg` | `#ffffff` | ✓ Surface/Page | |
| `--lc-body-bg` | `#F9FAFB` | ✗ | Tailwind gray-50 |
| `--lc-bg-secondary` | `#FAFAFA` | ✓ functional/background/background-sidenavbar | |
| `--lc-bg-tertiary` | `#f1f5f9` | ✗ | Tailwind slate-100 |
| `--lc-bg-hover` | → `--core-neutral-gray-100` `#EEEEEE` | ✓ Surface/Hover (value) | |
| `--lc-text` | `#1e293b` | ✗ (Text/Primary is `#121212`) | Tailwind slate-800 |
| `--lc-text-secondary` | → `--semantic-text-secondary` | ✓ | |
| `--lc-text-muted` | `#999999` | ⚠ = Text/**Disabled** | Figma Text/**Muted** is `#707070` — the name doesn't match the value |
| `--lc-border` | `#e2e8f0` | ✗ (Border/Default is `#ededec`) | Tailwind slate-200 |
| `--lc-border-strong` | → `--core-neutral-gray-300` | ✓ (= Border/Focus value) | |
| `--lc-user-bg` | → `--brand-sefaria-blue` | ✓ | |
| `--lc-user-text` | `#ffffff` | ✓ Text/Inverse | |
| `--lc-assistant-bg` | `#f1f5f9` | ✗ | **unused** |
| `--lc-assistant-text` | `#1e293b` | ✗ | **unused** |
| `--lc-error` | `#ef4444` | ✗ (Red/700 is `#c03522`) | Tailwind red-500 |
| `--lc-danger` | `#C03522` | ✓ Core/Validation/Red/700 | |
| `--lc-danger-hover` | `#A02C1C` | ✓ Core/Validation/Red/800 | |
| `--lc-sefaria-blue` | → `--sefaria-blue` | ⚠ | `--sefaria-blue` is **not defined in the widget**; it only works because Sefaria's `s2.css` defines it on the host page. In the demo page these rules fall back to inherited color. Used in 7 places (links, feedback modal, limit message). |
| `--lc-disabled-button` | `#e6e6e6` | ✓ Core/Neutral/Gray/250 | |
| `--lc-disabled-text` | `#999` | ✓ Text/Disabled | same value as `--lc-text-muted` |
| `--lc-submit-white` | `#FBFDFE` | ✗ | near-white, used on submit button text |
| `--lc-on-primary` | → `--core-base-white` | ✓ | **unused** |
| `--lc-icon-primary` | → `--semantic-icon-muted` | ✓ | |
| `--lc-topics-bg` | → `--core-blue-tbr-100` | ✓ | **unused** |
| `--lc-tooltip-bg` | `#3a3a3a` | ✗ | read by `src/lib/tooltip.js` |
| `--lc-tooltip-text` | → `--core-base-white` | ✓ | read by `src/lib/tooltip.js` |
| `--lc-shadow` | `rgb(0 0 0 / .1)` ×2 | ✗ (Figma Shadows differ) | |
| `--lc-icon-muted-filter` / `-default-filter` / `-disabled-filter` | CSS filters | ✓ produce the 3 icon tokens | needed because `<img>` icons can't take `color` |

### Referenced but never defined (the fallback value is always used)

| Variable | Fallback | Where | Figma |
|---|---|---|---|
| `--core-neutral-gray-200` | `#ededec` | `.history-empty-icon` L3383 | ✓ Core/Neutral/Gray/200 |
| `--semantic-surface-hover` | `#eee` | `.delete-modal .skip:hover` L4215 | ✓ Semantic/Surface/Hover |
| `--sefaria-blue` | none | see `--lc-sefaria-blue` | — |

## 2. Hard-coded colors (not using a token)

All in `src/components/LCChatbot.svelte` unless noted.

### Text and icon colors

| Line | Selector | Value | Figma match |
|---|---|---|---|
| 3231 | `.history-row.active .history-row-title` | `#121212` | ✓ Text/Primary |
| 4187 | `.delete-modal .feedback-modal-title` | `#121212` | ✓ Text/Primary |
| 3242 | `.history-row-date` | `#999` | ✓ Text/Disabled (or Text/Muted `#707070`?) |
| 3650 | `.empty-state … a` | `#575757` | ✓ Text/Secondary (token exists) |
| 3773 | `.retry-btn:hover` | `#dc2626` | ✗ Tailwind red-600 |
| 4457 | `.progress-trail-toggle` | `#888` | ✗ |
| 4463 | `.progress-trail-toggle:hover` | `#555` | ✗ (≈ Gray/700 `#575757`) |
| 4479 | `.progress-trail-entry` | `#777` | ✗ (≈ Text/Muted `#707070`) |
| 4482 | `.progress-trail-entry--error` | `#c62828` | ✗ (≈ Red/700 `#c03522`) |
| 4485 | `.progress-trail-entry--complete` | `#666` | ✓ Core/Neutral/Gray/600 |
| 4512, 4526 | `.trail-ref-link`, `.trail-ref-icon` | `#18345D` | ✓ Text/Link (token exists) |
| 4519 | `.trail-ref-link:hover` | `#465D7D` | ✗ (same value as `--lc-primary-hover`) |
| 2872, 3979, 4114 | trigger, `.send-btn`, `.settings-save` | `white` | ✓ Icon/Text Inverse |
| `tooltip.js` 75 | tooltip text fallback | `#fff` | ✓ |

### Backgrounds and borders

| Line | Selector | Value | Figma match |
|---|---|---|---|
| 2891 | `.lc-chatbot-trigger:active` | `#0B1A2D` | ✗ |
| 3198 | `.history-row:hover` | `#f0f7ff` | ✓ Core/blue TBR/100 (token exists) |
| 3202, 3208 | `.history-row.active` (+hover) | `#ddeeff` | ✓ Core/blue TBR/200 |
| 3683 | `.message.failed .message-content` | `#fef2f2` | ✗ Tailwind red-50 |
| 4209 | `.delete-modal .skip` border | `#ccc` (fallback) | ✓ Gray/300 (token exists) |
| 4211 | `.delete-modal .skip` bg | `#fff` (fallback) | ✓ (token exists) |
| 2523 | `.sheet-grabber` | `#cbd5e1` (fallback to a defined token — never used) | — |
| 4498–4499 | `.progress-trail-spinner` border | `#ccc` / `#888` | ✓ Gray/300 / ✗ |
| `tooltip.js` 74 | tooltip bg fallback | `#3a3a3a` | ✗ |

### Transparent overlays, gradients and shadows

| Line | Selector | Value |
|---|---|---|
| 2445 | docked panel shadow | `rgba(0,0,0,.08)`, `rgba(13,3,32,.16)` (close to Figma shadow-large, which uses `#0D032029`) |
| 2534 | fullscreen sheet shadow | `rgb(0 0 0 / .08)`, `rgb(0 0 0 / .22)` |
| 3300 | `.history-row-dropdown` shadow | `rgba(13,3,32,.14)` |
| `tooltip.js` 84 | tooltip shadow | `rgba(0,0,0,.25)` |
| 4148 | `.feedback-modal-overlay` | `rgba(0,0,0,.4)` |
| 3160 | `.history-list-fade` | `rgba(250,250,250,.7 → .2)` (= `--lc-bg-secondary` with alpha) |
| 3867–3869 | `.lc-thinking-label-base` shimmer | `rgba(255,255,255,.75 / 1)` |
| 4515, 4520 | `.trail-ref-link` underline | `rgba(24,52,93,.3)`, `rgba(70,93,125,.6)` |
| 2613 | fullscreen tap highlight | `rgb(0 0 0 / .08)` |

## 3. Colors baked into icon SVG files (`public/static/icons/`)

These only show up where no CSS filter recolors them. Every `<img>` icon in the widget currently gets a filter,
so none of these colors appear on screen today.

| Color | Files |
|---|---|
| `#666666` | `ellipsis-vertical`, `picture-in-picture-2`, `toggle-right`, `info`, `circle-question-mark`, `flask-conical`, `minimize`, `panel-right-close`, `rotate-ccw` |
| `#C03522` | `trash-2-danger` (✓ Red/700, intentionally not filtered) |
| `white` | `logo`, `toggle-right`, `circle-question-mark` |
| `currentColor` | all others |

## 4. Summary of issues

1. **Tailwind leftovers with no Figma match:** `--lc-text`, `--lc-border`, `--lc-bg-tertiary`, `--lc-body-bg`, `--lc-error`, `#dc2626`, `#fef2f2`, `#cbd5e1`.
2. **Name doesn't match value:** `--lc-text-muted` is `#999` (Text/Disabled), but Figma Text/Muted is `#707070`.
3. **Duplicates:** `#18345D` is defined 3 times (`--semantic-action-primary`, `--semantic-text-link`, `--brand-sefaria-blue`). `#999` is defined twice (`--lc-text-muted`, `--lc-disabled-text`).
4. **Hard-coded values that already have a token:** `#575757`, `#18345D`, `#f0f7ff`, `#465D7D`, `#ccc`, `#fff`.
5. **Undefined variables:** `--sefaria-blue` depends on the host page; `--core-neutral-gray-200` and `--semantic-surface-hover` always fall back.
6. **Unused tokens:** `--semantic-text-muted`, `--lc-assistant-bg`, `--lc-assistant-text`, `--lc-on-primary`, `--lc-topics-bg`.
7. **Progress trail** (`.progress-trail-*`) uses its own greys (`#888`, `#555`, `#777`, `#666`, `#ccc`) and red (`#c62828`), none of which come from tokens.

## 5. Where the non-Figma colors appear in the product

### Non-Figma tokens

| Token | Value | Where it shows up |
|---|---|---|
| `--lc-text` | `#1e293b` | Chat history row titles · rename input · ⋮ menu item labels (New chat, Dock, Feedback, Help, Opt out) · history row dropdown (Rename) · header icon button text color (no visible effect — icons are images) · moderator Settings panel title, inputs, Reset hover |
| `--lc-border` | `#e2e8f0` | Header bottom divider · input bar top divider and textarea border · history panel side divider · history search box · rename input · history list top divider · ⋮ menu dropdown and row dropdown outlines · "couldn't load chats" error divider · loading spinner ring · feedback form textarea · Settings panel inputs and Reset button |
| `--lc-text-muted` | `#999` (name says Muted, value is Disabled) | Input placeholder ("What are you learning today?") · message timestamps · loading text · history empty-state subtext ("Conversations you start…") · "couldn't load chats" error · Settings note |
| `--lc-bg-tertiary` | `#f1f5f9` | Hover background on ⋮ menu items and on the history row dropdown (Rename/Delete) · Settings Reset button |
| `--lc-body-bg` | `#F9FAFB` | Main panel background (floating, docked, and mobile fullscreen) · chat body behind the messages |
| `--lc-primary-hover` | `#465D7D` | Hover on Send button · Settings Save · feedback/delete modal primary button |
| `--lc-error` | `#ef4444` | Failed assistant message border · Retry link · Settings error text |
| `--lc-submit-white` | `#FBFDFE` | Text on the feedback/delete modal primary button |
| `--lc-shadow` | 2× black 10% | Launcher button (floating "Library Assistant" pill) · floating panel · ⋮ menu dropdown · feedback/delete modal |
| `--lc-tooltip-bg` | `#3a3a3a` | All tooltips (header icons, truncated history titles, location tag) |
| `--lc-sefaria-blue` | host page's `--sefaria-blue` | Links inside assistant replies · feedback modal title, subtitle, primary button bg, Skip button · "Thanks for your feedback" text · "Start a new chat" link in the turn-limit message |
| `--lc-disabled-text` (Figma value, duplicate of `--lc-text-muted`) | `#999` | Disabled "New chat" menu item · feedback form dropdown placeholder and textarea placeholder · disabled feedback Submit |

### Hard-coded values

| Value | Where it shows up |
|---|---|
| `#0B1A2D` | Launcher pill background while pressed |
| `#f0f7ff` | Chat history row hover |
| `#ddeeff` | Selected (current) chat history row |
| `#121212` | Selected chat history row title · delete-chat modal title |
| `#999` | Dates on chat history rows |
| `#575757` | Links in the welcome message |
| `#dc2626` | Retry link hover (failed message) |
| `#fef2f2` | Failed assistant message background |
| `#18345D` | Source links in the "thinking" progress trail |
| `#465D7D` + `rgba(70,93,125,.6)` | Progress-trail source link hover color and underline |
| `rgba(24,52,93,.3)` | Progress-trail source link underline |
| `#777` | Progress-trail step text |
| `#666` | Completed progress-trail steps |
| `#c62828` | Failed progress-trail steps |
| `#888`, `#555` (toggle) · `#ccc`, `#888` (spinner) | **Nowhere** — `.progress-trail-toggle` / `.progress-trail-spinner` aren't rendered (dead CSS) |
| `#18345D` (`.trail-ref-icon`) | **Nowhere** — not rendered |
| `#cbd5e1` | **Nowhere** — fallback for the mobile sheet grabber bar; the defined token (`#CCCCCC`) always wins |
| `#ccc`, `#fff`, `#eee` (fallbacks) | Delete-chat modal Cancel button border, background, hover (`#eee` is always used, since `--semantic-surface-hover` is undefined) |
| `#ededec` (fallback) | Circle behind the history empty-state icon (always used, since `--core-neutral-gray-200` is undefined) |
| `white` | Launcher pill text · Send arrow · Settings Save text |
| `rgba(0,0,0,.08)` + `rgba(13,3,32,.16)` | Docked panel shadow |
| `rgb(0 0 0/.08)` + `rgb(0 0 0/.22)` | Mobile bottom sheet shadow once lifted |
| `rgba(13,3,32,.14)` | History row ⋮ dropdown shadow |
| `rgba(0,0,0,.25)` | Tooltip shadow |
| `rgba(0,0,0,.4)` | Dimmed backdrop behind feedback/delete modals |
| `rgba(250,250,250,.7→.2)` | Fade at the bottom of the chat history list |
| `rgba(255,255,255,.75/1)` | Shimmer sweep on the "Thinking…" label |
| `rgb(0 0 0/.08)` | Mobile tap highlight on topic and source links |

## 6. Experiment: text, action and feedback colors swapped to Figma tokens (poc/penina-icon-cleanup)

New `:host` tokens: `--semantic-text-primary`, `--semantic-text-inverse`, `--semantic-text-link-hovered`,
`--semantic-action-primary-hover`, `--semantic-action-primary-pressed`, `--semantic-feedback-error-{text,border,background}`.

- `--lc-text` → Text/Primary · `--lc-text-muted` → Text/Muted · `--lc-submit-white` → Text/Inverse · `--lc-primary-hover` → Action/Primary hover
- History row dates, progress-trail step text (running + complete) → Text/Muted
- `--lc-sefaria-blue` removed: reply links, turn-limit link, "Thanks" → Text/Link; modal title and Skip → Text/Primary; modal subtitle → Text/Secondary; modal primary button → Action/Primary
- Progress-trail links → Text/Link, hover → Text/Link hovered, underlines via `color-mix()` at the old opacities
- Launcher pressed → Action/Primary pressed
- `--lc-error` removed: failed-message border → Error/Border; Retry and Settings error → Error/Text; failed bg → Error/Background; failed trail step → Error/Text
- Retry hover (no Figma token) → existing `--lc-danger-hover` (Red/800)

### 6b. Components use semantic tokens directly (no `--lc-*` color aliases for text/action/feedback)

Removed aliases: `--lc-primary`, `--lc-primary-hover`, `--lc-text`, `--lc-text-secondary`, `--lc-text-muted`, `--lc-user-text`,
`--lc-disabled-text`, `--lc-submit-white`, `--lc-icon-primary`, `--lc-tooltip-text`, `--lc-on-primary`, plus unused
`--lc-assistant-bg`, `--lc-assistant-text`, `--lc-topics-bg`. Added `--semantic-text-disabled`, `--semantic-icon-inverse`,
`--semantic-module-primary`. Semantic tokens in `:host` are grouped by Figma collection.

- Launcher, Send, Settings Save backgrounds → Action/Primary; header title, sparkle, response headings, "sending" status → Module/Primary; response links, Settings back → Text/Link
- Delete option and history error text → Feedback/Error/Text
- Hard-coded `white`, `#121212`, `#575757` → Text/Inverse, Icon/Inverse, Text/Primary, Text/Secondary

Still non-semantic, pending Figma tokens: `--lc-danger` / `--lc-danger-hover` (delete confirm button, Retry hover) — propose
Semantic/Action/Danger (+ hover). `tooltip.js` keeps `'#fff'` / `'#3a3a3a'` as JS fallbacks.
