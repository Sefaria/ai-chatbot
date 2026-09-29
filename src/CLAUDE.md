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

Bot version and prompt slugs configured via settings panel (gear icon).

## Host asks

A host page can open the widget with a prompt: set `window.lcChatbotPendingAsk = { text, intent }`
and dispatch `new Event('chatbot:ask')` on `document`. The widget takes it on mount or on the
event (whichever comes first), starts a fresh conversation and sends `text`. With
`intent: 'personalize'` it first runs the Personalize Responses questions (logged-out visitors
too), then sends `text` with the memory they produced.

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
