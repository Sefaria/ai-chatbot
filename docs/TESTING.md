# Testing

## Quick Reference

```bash
pytest                                              # Local (SQLite, fast)
pytest --ds=chatbot_server.test_settings_postgres   # Local (PostgreSQL)
```

## Strategy

- **Local development**: SQLite in-memory (fast, no setup)
- **CI**: PostgreSQL (matches production, catches DB-specific issues)

## Local PostgreSQL Setup (Optional)

```bash
createdb ai_chatbot_test
pytest --ds=chatbot_server.test_settings_postgres
```

## Git Hooks

- **pre-commit**: Runs ruff linting on staged Python files

---

## E2E (Playwright)

### How to run

```bash
npm run build          # Build the widget bundle first (required)
npm run test:e2e       # Run the e2e tests with Playwright (Chromium)
```

Prerequisites: `npx playwright install chromium` (one-time, downloads ~94 MB).

### What's covered

| Test file | Behavior tested |
|---|---|
| `e2e/trail-ref-link.spec.js` | A `tool_start` SSE event with `refData.is_ref=true` renders a clickable `<a class="trail-ref-link">` inside the thought accordion with the correct `href` (`https://www.sefaria.org/Genesis.1.1`), label (`Genesis 1:1`), and `target="_blank"`. |
| `e2e/location-pin.spec.js` | On a Sefaria.org page at `/Genesis.1.1`, when `GET /api/ref/Genesis.1.1` returns 404, the widget falls back to the tref path and renders `<a class="lc-location-tag">` on the user message bubble with the correct `href` and label. |
| `e2e/topic-appetizer.spec.js` | Clicking a topic button (`button.lc-topic-link`) in the appetizer accordion dispatches `sefaria:bootstrap-url` on `document` with `detail.url === '/topics/shabbat'`. |
| `e2e/scroll-behavior.spec.js` | On final response with a tall response package, the canvas auto-scrolls so the package top sits ~80px below the canvas top (not pinned to the bottom), using `scroll-behavior: smooth`. Regression guard for the `NodeList.at()` bug in `scrollToResponseStart`. |

### Mock strategy

All tests use a **fully mocked backend** — no real server is needed. Mocks are applied via Playwright's `page.route()`:

| Route pattern | Mock response |
|---|---|
| `**/v2/prompts/defaults` | `{"corePromptSlug":"test","labs":false}` |
| `**/history**` | `{"messages":[],"hasMore":false,"session":null}` |
| `**/v2/chat/client-event` | `{}` (telemetry sink) |
| `**/v2/chat/recover` | `{}` |
| `**/chat/stream` | `text/event-stream` body with appetizer → status → tool_start → tool_end → message events |
| `https://www.sefaria.org/api/ref/**` | 404 (location-pin test only, to exercise the tref fallback path) |

The SSE body is built by `buildSseBody(markdown)` in `e2e/helpers.js`; tests can pass a long `markdown` to force a tall response package (used by the scroll test).

### Hostname trick (sefaria.org origin)

All tests load the widget from a **fake `https://www.sefaria.org/Genesis.1.1` page** rather than localhost. This is required because:

1. **Location pin** — `isSefariaHostname(window.location.hostname)` must return `true` for `parseSefariaRef` to run and for `extractCandidateTref` to extract the tref from the URL path.
2. **Topic appetizer** — `handleAppetizerClick` checks `window.location.hostname.includes('sefaria.org')` to decide whether to dispatch `sefaria:bootstrap-url` in-page or open a new tab.

The fixture is served by two `page.route()` intercepts:
- `https://www.sefaria.org/Genesis.1.1` → HTML with `<lc-chatbot ...>` and a module script tag pointing to `/__lc_bundle.js`.
- `https://www.sefaria.org/__lc_bundle.js` → contents of `dist/lc-chatbot.js` read from disk.

This makes `window.location` appear as `https://www.sefaria.org/Genesis.1.1` without running a real server or doing DNS tricks.

### Selectors used

| Element | Selector |
|---|---|
| Send button | `button.send-btn` |
| Message input | `textarea` |
| Trail ref link | `a.trail-ref-link` |
| Location tag | `a.lc-location-tag` |
| Location label | `.lc-location-ref` |
| Topics accordion toggle | `button.lc-accordion-header` (hasText: "Show related topics") |
| Topic button | `button.lc-topic-link` (hasText: "Shabbat") |

Playwright's CSS locators pierce the open shadow DOM automatically — no special shadow-piercing syntax is needed.

### Test 3 verdict — topic navigation

The topic click dispatches `/topics/shabbat` correctly. The `handleAppetizerClick` handler in `LCChatbot.svelte` uses `document.dispatchEvent(new CustomEvent('sefaria:bootstrap-url', { detail: { url: '/topics/shabbat' } }))` when `window.location.hostname.includes('sefaria.org')`. No bug found.
