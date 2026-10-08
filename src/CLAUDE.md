# Frontend (Svelte)

Web component widget for embedding the chatbot.

## Key Files

```
src/
├── main.js                      # Entry point, registers web component
├── components/
│   └── LCChatbot.svelte         # Main widget (single-file component)
├── i18n/
│   ├── index.js                 # svelte-i18n setup and locale store wiring
│   └── locales/
│       ├── en.json              # source-of-truth strings (translated via Weblate)
│       └── he.json              # Hebrew translations
└── lib/
    ├── api.js                   # HTTP client, SSE streaming
    ├── session.js               # Session management
    ├── storage.js               # localStorage persistence
    └── markdown.js              # Markdown rendering
```

## Commands

```bash
npm run dev      # Dev server at :5173
npm run build    # Build bundle to dist/
```

## Patterns

- **Svelte 5** with runes (`$state`, `$derived`, `$effect`)
- **Single-file component** - all widget logic in LCChatbot.svelte
- **Web Component** - registered as `<lc-chatbot>` custom element
- **SSE streaming** - real-time responses via Server-Sent Events

## Analytics (GA4)

Events (`assistant_click`, `assistant_element_shown`, `assistant_message_sent`) go
through the `track()` helper in `LCChatbot.svelte`. **Never call `window.gtag`
directly** — `track()` is the only place `is_staff` and `la_version` are attached,
and analysts filter on `is_staff` to exclude internal traffic, so a bypassing event
silently skews their reports.

Label a click or impression by adding `data-feature-name` / `data-element-shown-name`
to the element; host-level listeners pick it up across the shadow-DOM boundary.

## Widget Attributes

| Attribute | Type | Required | Description |
|-----------|------|----------|-------------|
| `user-id` | string | No | Encrypted user token. Omit for logged-out visitors: they chat under an anonymous id (`localStorage`) until the server's free responses run out, then see a login prompt |
| `api-base-url` | string | Yes | Backend API URL |
| `placement` | `"left"` \| `"right"` | No | Corner placement |
| `default-open` | boolean | No | Open on load |
| `max-input-chars` | number | No | Max characters allowed in the textarea (default: 10000) |
| `max-prompts` | number | No | Max prompts per conversation before blocking (default: 100) |
| `mode` | `"floating"` \| `"panel"` | No | Display mode |
| `origin` | string | No | Origin identifier for Braintrust trace tagging |
| `is-moderator` | boolean | No | Staff flag (host sets it from `request.user.is_staff`) — shows settings gear, tags Braintrust, and emits `is_staff` on every GA4 event |
| `interface-lang` | `"en"` \| `"he"` | No | Interface language |
| `login-url` | string | No | Login page for the anonymous login prompt (default `/login`); `?next=<current page>` is appended at click time |
| `hide-launcher-on-scroll` | boolean | No | On phones, hide the closed Ask pill while the page scrolls. Sefaria sets it on reading pages only: a text, a single topic, a sheet (reading or editing) |

Bot version and prompt slugs configured via settings panel (gear icon).

## Host Events

- Dispatches `chatbot:opened` / `chatbot:closed` on `document`, and `sefaria:bootstrap-url` for in-page navigation (always via `navigateHost()`).
- Listens for `chatbot:open` on `document` to open from host UI (Sefaria's mobile menu, no-results button). `detail.source` becomes the GA4 `feature_name`; an optional `detail.question` is always asked in a new chat, never added to the open conversation (signed-in users included). With `source: 'search_no_results'` the question is sent with `entrySource: "search_no_results"`, which the server never lets use up a logged-out visitor's last free answer (whatever the limit; once per visitor). Past the free-answer limit, a question is not sent and no new chat starts: the panel opens on the visitor's last conversation, with the login prompt in place of the input.
- Listens for `chatbot:launcher` on `document`: `detail = { source, question }` relabels the closed pill with the widget's own label for that source (`search_no_results` → `assistant.floating_button.search_label`, "✦ Search with Library Assistant") and makes a click ask `question` in a new chat, as `chatbot:open` would. It asks once: later clicks with the same source and question (e.g. after closing the assistant, or on another empty tab of the same search) just reopen the assistant; `detail = null` puts the "✦ Ask" pill back.

## Launcher

The closed widget is a "✦ Ask" pill in the corner, on phones and desktop. It is purple (`--lc-entry-bg*`, set on `.lc-chatbot-container`); once open, the assistant keeps the Sefaria blue theme. A reply that lands while the assistant is closed puts a badge on the pill and is announced to screen readers until the assistant is opened.

## Phones

Under `FULLSCREEN_QUERY` (≤600px wide, or ≤500px tall) the widget is a full-screen sheet, not a floating/docked panel:

- Never opens on load, whatever `default-open` or saved state says. On pages with `hide-launcher-on-scroll`, the closed launcher steps aside while the page scrolls and returns once scrolling stops.
- Owns one history entry (`lib/sheetHistory.js`), so Back closes it. Its popstate listener is registered at bundle load so it runs before the host's; Sefaria loads the bundle in `<head>`. In-page links close the sheet and navigate after the pop.
- Locks page scroll. The open container is a full-screen backdrop in the panel's colour, and only the panel follows `visualViewport`, so the input stays above the keyboard and the page never flashes through while the keyboard closes. Every text field is 16px (smaller makes iOS zoom).
- One step up the type scale (`--lc-font-size*` redefined on `.mode-fullscreen`). Buttons are at least 44×44px, with 24px icons (22px in menus and feedback); inline links get block padding for a taller tap area.
- The header is a drag handle (grabber bar on top, buttons excluded): dragging down more than 120px, or a quick flick, closes the sheet; a shorter drag springs back.
- No dock mode. History covers the chat; picking a chat returns to it.

## i18n

User-facing strings live in `src/i18n/locales/{en,he}.json` and are looked up via `$_('key')` (svelte-i18n). Production translations are managed in Weblate at:

- `https://weblate.sefaria.org/projects/ai-chatbot/`

Adding a new string:

1. Add the key to `en.json` (source of truth).
2. Use `$_('your.key')` in templates or `get(_)('your.key')` in JS contexts.
3. Don't translate dev-facing logs/errors (e.g. anything in `lib/api.js` that the user never sees).
4. Merge to `main`; Weblate will pick up the new key and surface it for translation (target SLA: within ~5 minutes after sync).

Translation delivery convention:

- Translators edit in Weblate.
- Weblate opens PRs against `main` (no direct pushes to `main`).
- Engineers review and merge translation PRs in GitHub.

Conventions for Weblate compatibility:
- Flat dotted keys (`section.subsection.name`), grouped by UI area.
- Pass full sentences as a single key — never concatenate translated fragments. Use ICU placeholders (`{count}`, plurals) when interpolating.
- Don't reuse a key with a changed meaning. If meaning shifts, create a new key so Weblate flags it for re-translation.
- `he.json` may be empty or partial; missing keys fall back to `en.json` via `fallbackLocale: 'en'`.

The `interface-lang` attribute accepts `en` or `he` and is piped directly into the svelte-i18n locale store via `setLocale` in `src/i18n/index.js`.
