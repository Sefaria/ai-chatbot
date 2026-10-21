# LC Chatbot

Embeddable AI chat widget for Jewish text learning. Built with Svelte and Django, powered by Claude with Sefaria tool access.

## Quick Start

```bash
git clone <repo-url>
cd ai-chatbot
```

Follow the full onboarding guide: [docs/FRESH_INSTALL.md](docs/FRESH_INSTALL.md).

Recommended local run flow:

```bash
# terminal 1 (backend)
cd server
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# set up postgres and update .env with your DB credentials and API keys
python manage.py migrate
python manage.py runserver 0.0.0.0:8001

# terminal 2 (frontend)
cd ..
npm install
cp .env.example .env.local
npm run dev
```

Visit `http://localhost:5173` for the local widget. It opens logged out; add `?user-id=<encrypted user token>` to open it as that user.

## Widget Usage

```html
<script type="module" src="https://your-cdn.com/lc-chatbot.js"></script>

<lc-chatbot
  user-id="user-123"
  api-base-url="https://api.example.com"
></lc-chatbot>
```

### Attributes

| Attribute | Type | Required | Description |
|-----------|------|----------|-------------|
| `user-id` | string | No | Encrypted user token. Leave it out for a logged-out visitor: the widget then identifies them with an anonymous ID it keeps in localStorage, and when it loads logged out it deletes any chat a logged-in user left in the browser |
| `api-base-url` | string | Yes | Base URL for the chat API |
| `placement` | `"left"` \| `"right"` | No | Corner placement |
| `default-open` | boolean | No | Open on load |

### Slots

| Slot | Description |
|------|-------------|
| `signup-options` | The host page's sign-up options, shown under the text of the login prompt (a logged-out visitor who used up their free answers), centred. Put them in a child element: `<lc-chatbot><div slot="signup-options">…</div></lc-chatbot>`. They stay in the host page's DOM, so its own styles and scripts apply. With no such child, the prompt shows its text only |

Bot version and prompt slugs can be configured from the widget settings panel (gear icon).

## API

### POST /api/chat/stream (alias: /api/v2/chat/stream)

Send a message and receive a streamed response with Server-Sent Events.

### POST /api/v2/chat/feedback

Send user feedback tied to a response trace.

### GET /api/history

Load conversation history with session metadata.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for full API reference.

## Development

```bash
# Frontend
npm install
npm run dev      # Dev server at :5173
npm run build    # Build bundle

# Backend
cd server
source venv/bin/activate
python manage.py runserver 0.0.0.0:8001
pytest           # Run tests
```


## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | Yes | PostgreSQL connection for Django |
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `CHATBOT_USER_TOKEN_SECRET` | Yes | Secret used to decrypt encrypted `userId` tokens |
| `BRAINTRUST_API_KEY` | No | Braintrust prompts/logging |
| `BRAINTRUST_PROJECT` | No | Braintrust project name |
| `SENTRY_DSN` | No | Enable Sentry error monitoring for Django + agent errors |
| `SENTRY_TRACES_SAMPLE_RATE` | No | Sentry performance trace sampling rate (default `0.0`) |
| `SENTRY_PROFILES_SAMPLE_RATE` | No | Sentry profiling sampling rate (default `0.0`) |
| `DJANGO_SECRET_KEY` | No | Django secret |
| `DJANGO_DEBUG` | No | Debug mode |
| `VITE_SEFARIA_HOSTNAMES` | No | Comma-separated hostnames the frontend should treat as Sefaria pages, for local ref detection. Empty by default; `.env.example` sets localhost |

Create a `.env` file in the `server/` directory with backend API keys. For frontend-only local values, copy root `.env.example` to `.env.local`.

## Documentation

- [Architecture](docs/ARCHITECTURE.md) - System design and API reference
- [Fresh Install](docs/FRESH_INSTALL.md) - Local setup and troubleshooting guide
- [Testing](docs/TESTING.md) - Test commands and CI details

## License

MIT
