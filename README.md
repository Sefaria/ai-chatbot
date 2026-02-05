# LC Chatbot

Embeddable AI chat widget for Jewish text learning. Built with Svelte and Django, powered by Claude with Sefaria tool access.

## Quick Start

```bash
./setup.sh   # Install deps, create venv, run migrations
./start.sh   # Start backend (8001) + frontend (5173)
```

Visit `http://localhost:5173` to see the widget.

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

### POST /api/v2/chat/stream

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
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `BRAINTRUST_API_KEY` | No | Braintrust prompts/logging |
| `BRAINTRUST_PROJECT` | No | Braintrust project name |
| `DJANGO_SECRET_KEY` | No | Django secret |
| `DJANGO_DEBUG` | No | Debug mode |

Create a `.env` file in the `server/` directory with your API keys.

## Documentation

- [Architecture](docs/ARCHITECTURE.md) - System design and API reference
- [Testing](docs/TESTING.md) - Test commands and CI details

## License

MIT
