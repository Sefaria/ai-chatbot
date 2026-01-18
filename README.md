# LC Chatbot

A beautiful, embeddable chat widget built as a Web Component with Svelte, backed by a Django REST API with a **routed Claude AI agent**.

## Architecture Overview

The chatbot implements a **multi-flow routing architecture**:

```
User Message → Router → Flow Selection → Claude Agent → Response
                 ↓              ↓              ↓
           Guardrails    Prompt+Tools    Tool Calling
```

**Flows:**
- **HALACHIC** - Practical Jewish law questions (higher guardrails, source-focused)
- **SEARCH** - Finding and comparing sources (full search toolset)
- **GENERAL** - Learning, discussion, conceptual questions (minimal tools)
- **REFUSE** - Guardrail-blocked requests

**Observability:**
- **LangSmith** - End-to-end tracing with spans for router, agent, and tools
- **Braintrust** - Prompt management, structured logging, and evaluations

## Features

- 🔀 **Flow-Based Routing** - Automatic classification to appropriate handling mode
- 🛡️ **Guardrails** - Prompt injection detection, content policy enforcement
- 🤖 **Claude AI Agent** - Powered by Claude with Sefaria tool calling
- 📊 **Braintrust Integration** - Prompt versioning, evals, and structured logging
- 🔍 **LangSmith Tracing** - Full observability with nested spans
- 💬 **Markdown Rendering** - Rich responses with headings, code blocks, links
- 📐 **Resizable Panel** - Drag to resize, dimensions persist
- 📜 **Infinite Scroll History** - Load older messages with date markers
- 🎨 **Themeable** - CSS custom properties
- 💾 **Local Persistence** - Session and UI state saved to localStorage
- ⚡ **Lightweight** - Single JS bundle

## Quick Start

### 1. Configure Environment

Create a `.env` file in the `server/` directory:

```bash
# Required: Anthropic API key
ANTHROPIC_API_KEY=sk-ant-your-key-here

# Optional: LangSmith tracing (https://smith.langchain.com)
LANGSMITH_API_KEY=lsv2_your-key-here
LANGSMITH_PROJECT=sefaria-chatbot

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

## API Reference

### POST /api/chat

Send a message and receive a routed response.

**Request:**
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

**Response:**
```json
{
  "messageId": "msg_reply_...",
  "sessionId": "sess_...",
  "timestamp": "2026-01-05T08:12:36.000Z",
  "markdown": "According to Jewish law...",
  "routing": {
    "flow": "HALACHIC",
    "decisionId": "dec_...",
    "confidence": 0.85,
    "wasRefused": false
  }
}
```

### POST /api/chat/stream

Same as `/api/chat` but with Server-Sent Events for real-time progress:

**Events:**
- `routing` - Flow decision with reason codes
- `progress` - Tool execution updates
- `message` - Final response
- `error` - Error details

### GET /api/history

Load conversation history with session metadata.

### GET /api/health

Health check with service status.

### POST /api/admin/reload-prompts

Invalidate prompt cache (reloads from Braintrust on next request).

## Routing System

The router classifies user intent using:

1. **Keyword patterns** - Hebrew/English terms for halacha, search, learning
2. **Guardrail checks** - Prompt injection, harassment, high-risk content
3. **Flow stickiness** - Maintains flow unless intent clearly shifts

### Reason Codes

Routing decisions include explainable reason codes:

```python
ROUTE_HALACHIC_KEYWORDS     # Detected halachic terms
ROUTE_SEARCH_INTENT         # User wants to find sources
ROUTE_GENERAL_LEARNING      # Conceptual/learning question
ROUTE_FLOW_STICKINESS       # Continuing previous flow
GUARDRAIL_PROMPT_INJECTION  # Blocked injection attempt
GUARDRAIL_HIGH_RISK_PSAK    # High-risk halachic question
```

## Tool Sets by Flow

| Flow | Tools |
|------|-------|
| HALACHIC | get_text, text_search, semantic_search, topic_details, links, search_in_book, clarify_name |
| SEARCH | All search tools + dictionaries, manuscripts, catalogue info |
| GENERAL | get_text, text_search, semantic_search, topic_details, calendar |

## Observability

### LangSmith Tracing

Configure LangSmith for full observability:

```bash
export LANGSMITH_API_KEY=lsv2_...
export LANGSMITH_PROJECT=sefaria-chatbot
```

**Trace structure:**
```
chat_turn (chain)
├── router (chain)
├── prompt_fetch (retriever)
├── claude_completion_1 (llm)
├── tool_get_text (tool)
├── claude_completion_2 (llm)
└── summary_update (chain)
```

### Braintrust Logging

Configure Braintrust for structured logging and evals:

```bash
export BRAINTRUST_API_KEY=bt-...
export BRAINTRUST_PROJECT=sefaria-chatbot
```

**Logged data:**
- User message, summary, flow
- Prompt IDs and versions
- Tools available/used
- Response, metrics (latency, tokens, cost)
- Environment, app version tags

## Project Structure

```
server/
├── chat/
│   ├── views.py              # Orchestrator pattern
│   ├── models.py             # Session, Message, RouteDecision, etc.
│   │
│   ├── router/               # Flow classification
│   │   ├── router_service.py # Main router
│   │   ├── guardrails.py     # Safety checks
│   │   └── reason_codes.py   # Explainable codes
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
│   ├── tracing/              # LangSmith integration
│   │   └── langsmith_tracer.py
│   │
│   └── summarization/        # Conversation context
│       └── summary_service.py
```

## Database Models

| Model | Purpose |
|-------|---------|
| `ChatSession` | Session state, flow, summary |
| `ChatMessage` | Messages with routing context |
| `RouteDecision` | Audit trail for routing |
| `ToolCallEvent` | Individual tool executions |
| `BraintrustLog` | Structured eval-ready logs |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `LANGSMITH_API_KEY` | No | LangSmith tracing |
| `LANGSMITH_PROJECT` | No | LangSmith project name |
| `BRAINTRUST_API_KEY` | No | Braintrust prompts/logging |
| `BRAINTRUST_PROJECT` | No | Braintrust project name |
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
