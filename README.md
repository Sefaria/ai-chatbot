# LC Chatbot

A beautiful, embeddable chat widget built as a Web Component with Svelte, backed by a Django REST API with Claude AI agent. Drop it into any website with a single `<lc-chatbot>` tag.

## Features

- 🤖 **Claude AI Agent** - Powered by Claude with Sefaria tool calling
- 💬 **Markdown Rendering** - Rich responses with headings, code blocks, lists, links
- 📐 **Resizable Panel** - Drag to resize, dimensions persist across sessions
- 📜 **Infinite Scroll History** - Load older messages with date markers
- 🎨 **Themeable** - Customize with CSS custom properties
- 💾 **Local Persistence** - Session, draft, and UI state saved to localStorage
- ⚡ **Lightweight** - Single JS bundle (~52KB gzipped)
- 🔒 **Secure** - HTML sanitization, safe link handling
- 📊 **Full Logging** - All messages logged with userId, sessionId, messageId, tool usage
- 📈 **Langfuse Integration** - Full tracing and observability

## Quick Start

### 1. Configure Environment

Create a `.env` file in the `server/` directory:

```bash
# Required: Anthropic API key
ANTHROPIC_API_KEY=your-anthropic-api-key

# Optional: Langfuse tracing (https://langfuse.com)
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com

# Optional: Django settings
DJANGO_SECRET_KEY=your-secret-key
DJANGO_DEBUG=True
```

### 2. Start the Django Server

```bash
cd server

# Create virtual environment (use Python 3.11+)
python3.11 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run migrations
python manage.py migrate

# Start server
python manage.py runserver
```

The API will be available at `http://localhost:8000/api/`.

### 2. Start the Frontend Dev Server

```bash
# In project root
npm install
npm run dev
```

Visit `http://localhost:5173` to see the widget in action.

## Installation (Production)

### Widget Bundle

```html
<script type="module" src="https://your-cdn.com/lc-chatbot.js"></script>
```

Or install via npm:

```bash
npm install lc-chatbot
```

```javascript
import 'lc-chatbot';
```

### Usage

Add the custom element anywhere in your HTML:

```html
<lc-chatbot 
  user-id="user-123"
  api-base-url="https://api.example.com"
></lc-chatbot>
```

That's it! The widget appears as a floating button in the corner.

## Widget Attributes

| Attribute | Type | Required | Description |
|-----------|------|----------|-------------|
| `user-id` | string | Yes | Unique identifier for the user |
| `api-base-url` | string | Yes | Base URL for the chat API |
| `placement` | `"left"` \| `"right"` | No | Corner placement (default: `"right"`) |
| `default-open` | boolean | No | Open on load (default: `false`) |

## Events

The widget dispatches custom events on the `document`:

```javascript
document.addEventListener('chatbot:opened', () => {
  console.log('Chat opened');
});

document.addEventListener('chatbot:closed', () => {
  console.log('Chat closed');
});

document.addEventListener('chatbot:message_sent', (e) => {
  console.log('Message sent:', e.detail.messageId, e.detail.sessionId);
});

document.addEventListener('chatbot:error', (e) => {
  console.error('Error:', e.detail.type, e.detail.error);
});
```

## Theming

Customize appearance with CSS custom properties:

```css
lc-chatbot {
  --lc-primary: #6366f1;
  --lc-primary-hover: #4f46e5;
  --lc-bg: #ffffff;
  --lc-bg-secondary: #f8fafc;
  --lc-text: #1e293b;
  --lc-text-secondary: #64748b;
  --lc-border: #e2e8f0;
  --lc-user-bg: #6366f1;
  --lc-user-text: #ffffff;
  --lc-assistant-bg: #f1f5f9;
  --lc-radius: 16px;
  --lc-shadow: 0 20px 25px -5px rgb(0 0 0 / 0.1);
}
```

### Dark Theme Example

```css
lc-chatbot {
  --lc-bg: #1e1e2e;
  --lc-bg-secondary: #181825;
  --lc-text: #cdd6f4;
  --lc-text-secondary: #a6adc8;
  --lc-border: #313244;
  --lc-assistant-bg: #313244;
  --lc-assistant-text: #cdd6f4;
}
```

## API Reference

### POST /api/chat

Send a message and receive a response.

**Request:**

```json
{
  "userId": "abc123",
  "sessionId": "sess_...",
  "messageId": "msg_...",
  "timestamp": "2026-01-05T08:12:34.000Z",
  "text": "User question here",
  "context": {
    "pageUrl": "https://example.com/page",
    "locale": "en",
    "clientVersion": "1.0.0"
  }
}
```

**Response:**

```json
{
  "messageId": "msg_reply_...",
  "sessionId": "sess_...",
  "timestamp": "2026-01-05T08:12:36.000Z",
  "markdown": "### Answer\nHere is **markdown**..."
}
```

### GET /api/history

Load conversation history.

**Query Parameters:**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `userId` | Yes | User identifier |
| `sessionId` | Yes | Session identifier |
| `before` | No | ISO timestamp, load messages before this time |
| `limit` | No | Max messages (default 20, max 100) |

**Response:**

```json
{
  "messages": [
    {
      "messageId": "msg_...",
      "sessionId": "sess_...",
      "userId": "abc123",
      "role": "user",
      "content": "Hello",
      "timestamp": "2026-01-05T08:10:00.000Z"
    },
    {
      "messageId": "msg_...",
      "sessionId": "sess_...",
      "userId": "abc123",
      "role": "assistant",
      "content": "Hi! How can I help?",
      "timestamp": "2026-01-05T08:10:02.000Z"
    }
  ],
  "hasMore": true
}
```

## Server Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key for Claude |
| `DJANGO_SECRET_KEY` | No | Django secret key (default: dev key) |
| `DJANGO_DEBUG` | No | Debug mode (default: True) |
| `LANGFUSE_PUBLIC_KEY` | No | Langfuse public key |
| `LANGFUSE_SECRET_KEY` | No | Langfuse secret key |
| `LANGFUSE_HOST` | No | Langfuse host (default: https://cloud.langfuse.com) |
| `SEFARIA_API_BASE_URL` | No | Sefaria API URL (default: https://www.sefaria.org) |
| `SEFARIA_AI_BASE_URL` | No | Sefaria AI API URL (default: https://ai.sefaria.org) |

### Database

The default configuration uses SQLite. For production, configure PostgreSQL in `settings.py`:

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'chatbot',
        'USER': 'your_user',
        'PASSWORD': 'your_password',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}
```

## Claude Agent

The chatbot uses Claude with tool calling to access the Sefaria Jewish library. The agent automatically:

- Searches texts in Hebrew, Aramaic, and English
- Retrieves specific text passages
- Looks up topics and cross-references
- Provides scholarly responses with source citations

### Available Tools

| Tool | Description |
|------|-------------|
| `get_text` | Retrieve text content from a specific reference |
| `text_search` | Search across the entire Jewish library |
| `english_semantic_search` | Semantic search on English embeddings |
| `get_links_between_texts` | Find cross-references to a passage |
| `search_in_book` | Search within a specific book |
| `search_in_dictionaries` | Search Jewish reference dictionaries |
| `get_topic_details` | Get information about topics |
| `get_current_calendar` | Get current Jewish calendar info |
| `clarify_name_argument` | Validate/autocomplete text names |
| `get_text_catalogue_info` | Get bibliographic info for a text |
| `get_available_manuscripts` | Get manuscript metadata |

### Langfuse Tracing

Enable [Langfuse](https://langfuse.com) for full observability:

```bash
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...
export LANGFUSE_HOST=https://cloud.langfuse.com  # EU region
# export LANGFUSE_HOST=https://us.cloud.langfuse.com  # US region
```

All agent calls, tool executions, and token usage are traced. View your traces in the [Langfuse dashboard](https://cloud.langfuse.com).

**What gets traced:**
- Each message send as a top-level trace
- Claude API calls as `generation` spans with token usage
- Tool executions as `span` observations
- User/session IDs, metadata, and tags

## Project Structure

```
.
├── src/                          # Frontend (Svelte)
│   ├── main.js                   # Entry point
│   ├── components/
│   │   └── LCChatbot.svelte      # Main widget component
│   └── lib/
│       ├── api.js                # API client
│       ├── dates.js              # Date formatting
│       ├── markdown.js           # Markdown rendering
│       ├── session.js            # Session management
│       └── storage.js            # localStorage utilities
│
├── server/                       # Backend (Django)
│   ├── manage.py
│   ├── requirements.txt
│   ├── chatbot_server/           # Django project
│   │   ├── settings.py
│   │   ├── urls.py
│   │   └── wsgi.py
│   └── chat/                     # Chat app
│       ├── models.py             # Message & Session models
│       ├── views.py              # API endpoints
│       ├── serializers.py        # DRF serializers
│       ├── urls.py
│       └── agent/                # Claude Agent
│           ├── claude_service.py # Main agent service
│           ├── tool_schemas.py   # Tool definitions
│           ├── tool_executor.py  # Tool execution
│           └── sefaria_client.py # Sefaria API client
│
├── dist/                         # Built widget bundles
│   ├── lc-chatbot.js             # ES module
│   └── lc-chatbot.umd.cjs        # UMD module
│
├── package.json
├── vite.config.js
└── README.md
```

## Logging

All messages are logged to the database with:

- `userId` - User identifier
- `sessionId` - Session identifier  
- `messageId` - Unique message ID
- `timestamp` - Server timestamp
- `content` - Message text
- `latency_ms` - Response time (for assistant messages)
- `status` - Success/failure
- Context: `page_url`, `locale`, `client_version`

Query logs using Django ORM:

```python
from chat.models import ChatMessage

# Get all messages for a user
messages = ChatMessage.objects.filter(user_id='user-123')

# Get average response latency
from django.db.models import Avg
avg_latency = ChatMessage.objects.filter(
    role='assistant'
).aggregate(Avg('latency_ms'))
```

## Local Storage

The widget uses these namespaced localStorage keys:

| Key | Purpose |
|-----|---------|
| `lc_chatbot:size` | Panel dimensions `{ width, height }` |
| `lc_chatbot:session` | Session info `{ sessionId, lastActivity }` |
| `lc_chatbot:draft` | Draft message `{ text }` |
| `lc_chatbot:ui` | UI state `{ isOpen, placement }` |
| `lc_chatbot:messages:{sessionId}` | Cached messages |

## Session Management

Sessions auto-expire after 30 minutes of inactivity. A new session is created when:
- No session exists
- Last activity > 30 minutes ago
- Host passes `force-new-session="true"`

## Browser Support

- Chrome 80+
- Firefox 75+
- Safari 14+
- Edge 80+

Requires native support for Custom Elements v1 and ES2020.

## Development

### Frontend

```bash
npm install
npm run dev      # Start dev server
npm run build    # Build production bundle
```

### Backend

```bash
cd server
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

## License

MIT
