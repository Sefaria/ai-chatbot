# Frontend (Svelte)

Web component widget for embedding the chatbot.

## Key Files

```
src/
├── main.js                      # Entry point, registers web component
├── components/
│   └── LCChatbot.svelte         # Main widget (single-file component)
├── i18n/
│   ├── index.js                 # svelte-i18n setup, locale normalization
│   └── locales/
│       ├── en.json              # source-of-truth strings (translated via Weblate)
│       └── he.json              # Hebrew translations
└── lib/
    ├── api.js                   # HTTP client, SSE streaming
    ├── session.js               # Session management
    ├── storage.js               # localStorage persistence
    ├── markdown.js              # Markdown rendering
    └── dates.js                 # Date formatting (locale-aware)
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

## Widget Attributes

| Attribute | Type | Required | Description |
|-----------|------|----------|-------------|
| `user-id` | string | Yes | Encrypted user token |
| `api-base-url` | string | Yes | Backend API URL |
| `placement` | `"left"` \| `"right"` | No | Corner placement |
| `default-open` | boolean | No | Open on load |
| `max-input-chars` | number | No | Max characters allowed in the textarea (default: 10000) |
| `max-prompts` | number | No | Max prompts per conversation before blocking (default: 100) |
| `mode` | `"floating"` \| `"panel"` | No | Display mode |
| `origin` | string | No | Origin identifier for Braintrust trace tagging |
| `is-moderator` | boolean | No | Staff flag — shows settings gear and logged to Braintrust metadata |
| `interface-lang` | `"en"` \| `"he"` | No | Interface language |
| `welcome-messages` | JSON string | No | Localized welcome/restart messages |

Bot version and prompt slugs configured via settings panel (gear icon).

## i18n

User-facing strings live in `src/i18n/locales/{en,he}.json` and are looked up via `$_('key')` (svelte-i18n). Adding a new string:

1. Add the key to `en.json` (source of truth — Weblate watches this file).
2. Use `$_('your.key')` in templates or `get(_)('your.key')` in JS contexts.
3. Don't translate dev-facing logs/errors (e.g. anything in `lib/api.js` that the user never sees).

Conventions for Weblate compatibility:
- Flat dotted keys (`section.subsection.name`), grouped by UI area.
- Pass full sentences as a single key — never concatenate translated fragments. Use ICU placeholders (`{count}`, plurals) when interpolating.
- Don't reuse a key with a changed meaning. If meaning shifts, create a new key so Weblate flags it for re-translation.
- `he.json` may be empty or partial; missing keys fall back to `en.json` via `fallbackLocale: 'en'`.

The `interface-lang` attribute accepts `en` or `he` and is piped directly into the svelte-i18n locale store via `setLocale` in `src/i18n/index.js`. Date/time formatting in `lib/dates.js` reads the active locale from the store.
