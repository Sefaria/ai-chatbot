# Frontend (Svelte)

Web component widget for embedding the chatbot.

## Key Files

```
src/
├── main.js                      # Entry point, registers web component
├── components/
│   └── LCChatbot.svelte         # Main widget (single-file component)
└── lib/
    ├── api.js                   # HTTP client, SSE streaming
    ├── session.js               # Session management
    ├── storage.js               # localStorage persistence
    ├── markdown.js              # Markdown rendering
    └── dates.js                 # Date formatting
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
| `mode` | `"floating"` \| `"panel"` | No | Display mode |
| `max-input-chars` | number | No | Max characters allowed in the textarea (default: 500) |
| `origin` | string | No | Origin identifier for Braintrust trace tagging |
| `is-moderator` | boolean | No | Controls settings gear visibility |
| `is-staff` | boolean | No | Sefaria staff flag, logged to Braintrust metadata |
| `interface-lang` | `"english"` \| `"hebrew"` | No | Interface language |
| `welcome-messages` | JSON string | No | Localized welcome/restart messages |

Bot version and prompt slugs configured via settings panel (gear icon).
