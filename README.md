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
npm run dev
```

Visit `http://localhost:5173` for the local widget.

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
| `user-id` | string | Yes | Unique user identifier |
| `api-base-url` | string | Yes | Base URL for the chat API |
| `placement` | `"left"` \| `"right"` | No | Corner placement |
| `default-open` | boolean | No | Open on load |

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
| `DJANGO_SECRET_KEY` | No | Django secret |
| `DJANGO_DEBUG` | No | Debug mode |

Create a `.env` file in the `server/` directory with your API keys.

## Documentation

- [Architecture](docs/ARCHITECTURE.md) - System design and API reference
- [Fresh Install](docs/FRESH_INSTALL.md) - Local setup and troubleshooting guide
- [Testing](docs/TESTING.md) - Test commands and CI details

## License

MIT
