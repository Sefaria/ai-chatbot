# LC Chatbot

A beautiful, embeddable chat widget built as a Web Component with Svelte, backed by a Django REST API with a **Claude Agent SDK runtime**.

## Architecture Overview

The chatbot implements a **core-prompt agent architecture**:

```
User Message → Core Prompt (Braintrust) → Claude Agent SDK → Tool Calling → Response
```

**Observability:**
- **Braintrust** - Prompt management and Agent SDK tracing

## Versioned Agents

Agent logic is organized by version under `server/chat/`. The current implementation lives in `server/chat/V2/`.

To add a new version (e.g. V3):
1. Copy `server/chat/V2` to `server/chat/V3`.
2. Update your agent logic inside `server/chat/V3`.
3. Add URLs in `server/chat/urls.py` pointing to the new views.
4. Update `server/chat/views.py` health/version reporting (and prompt reloads if needed).
5. In the widget settings, set **Bot version** to `v3` to route requests to the new endpoints.

## Features

- 🤖 **Claude Agent SDK** - Claude with Sefaria tool calling
- 📊 **Braintrust Integration** - Core prompt management and tracing
- 💬 **Markdown Rendering** - Rich responses with headings, code blocks, links
- 📐 **Resizable Panel** - Drag to resize, dimensions persist
- 📜 **Infinite Scroll History** - Load older messages with date markers
- 🧩 **Versioned Agents** - Run multiple bot versions in parallel
- 🎨 **Themeable** - CSS custom properties
- 💾 **Local Persistence** - Session and UI state saved to localStorage
- ⚡ **Lightweight** - Single JS bundle

## Quick Start

### 1. Configure Environment

Create a `.env` file in the `server/` directory:

```bash
# Required: Anthropic API key
ANTHROPIC_API_KEY=sk-ant-your-key-here

# Optional: Braintrust prompts & evals (https://braintrust.dev)
BRAINTRUST_API_KEY=bt-your-key-here
BRAINTRUST_PROJECT=sefaria-chatbot

# Optional: Django settings
DJANGO_SECRET_KEY=your-secret-key
DJANGO_DEBUG=True
ENVIRONMENT=dev  # dev, staging, prod
```

### 2. Start the Django Server

```bash
cd server

# Create virtual environment (Python 3.11+)
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run migrations
python manage.py migrate

# Start server (port 8001)
python manage.py runserver 0.0.0.0:8001
```

The API will be available at `http://localhost:8001/api/`.

### 3. Start the Frontend Dev Server

```bash
# In project root
npm install
npm run dev
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

## API Reference

### WebSocket /api/ws/v2/chat

Send a message and receive progress updates and the final response over a websocket.

**Client → Server (JSON message):**
```json
{
  "userId": "abc123",
  "sessionId": "sess_...",
  "messageId": "msg_...",
  "timestamp": "2026-01-05T08:12:34.000Z",
  "text": "Is it permitted to cook on Shabbat?",
  "context": {
    "pageUrl": "https://example.com",
    "locale": "en"
  }
}
```

**Server → Client (progress event):**
```json
{
  "event": "progress",
  "type": "tool_start",
  "toolName": "get_text",
  "description": "Fetching text \"Genesis 1:1\""
}
```

**Server → Client (final message):**
```json
{
  "event": "message",
  "messageId": "msg_reply_...",
  "sessionId": "sess_...",
  "timestamp": "2026-01-05T08:12:36.000Z",
  "markdown": "According to Jewish law...",
  "toolCalls": [],
  "stats": {
    "llmCalls": 1,
    "toolCalls": 0,
    "latencyMs": 1200
  }
}
```

**Events:**
- `progress` - Tool execution updates
- `message` - Final response
- `error` - Error details

### POST /api/v2/chat/feedback

Send user feedback tied to a response trace.

### GET /api/v2/prompts/defaults

Fetch default prompt slugs for client settings.

### GET /api/history

Load conversation history with session metadata.

### GET /api/health

Health check with service status.

### POST /api/admin/reload-prompts

Invalidate prompt cache (reloads from Braintrust on next request).

## Observability
### Braintrust Tracing

Configure Braintrust for structured logging and evals:

```bash
export BRAINTRUST_API_KEY=bt-...
export BRAINTRUST_PROJECT=sefaria-chatbot
```

Braintrust auto-traces Claude Agent SDK calls, tool executions, and prompt usage.

## Project Structure

```
server/
├── chat/
│   ├── views.py              # Orchestrator pattern
│   ├── models.py             # Session, Message, RouteDecision, etc.
│   │
│   ├── prompts/              # Braintrust integration
│   │   ├── prompt_service.py # Fetch/cache prompts
│   │   └── default_prompts.py # Local fallbacks
│   │
│   ├── agent/                # Claude agent runtime
│   │   ├── claude_service.py # Main service
│   │   ├── tool_schemas.py   # Flow-organized tools
│   │   ├── tool_executor.py  # Execute tools
│   │   └── sefaria_client.py # Sefaria API
│   │
│   └── summarization/        # Conversation context
│       └── summary_service.py
```

## Database Models

| Model | Purpose |
|-------|---------|
| `ChatSession` | Session state and summary |
| `ChatMessage` | Messages with tool metadata |
| `RouteDecision` | Legacy routing audit trail (unused in V2) |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `BRAINTRUST_API_KEY` | No | Braintrust prompts/logging |
| `BRAINTRUST_PROJECT` | No | Braintrust project name |
| `CORE_PROMPT_SLUG` | No | Braintrust core prompt slug |
| `ENVIRONMENT` | No | dev/staging/prod |
| `DJANGO_SECRET_KEY` | No | Django secret |
| `DJANGO_DEBUG` | No | Debug mode |

## Development

### Frontend

```bash
npm install
npm run dev      # Dev server at :5173
npm run build    # Build bundle
```

### Backend

```bash
cd server
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 0.0.0.0:8001
```

## License

MIT
