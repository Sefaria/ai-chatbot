# Architecture

## Overview

LC Chatbot is an embeddable AI assistant for Jewish text learning. It uses a **core-prompt agent architecture**: user messages flow through a single Braintrust-managed system prompt into the Claude Agent SDK with Sefaria tool access.

```
Svelte Web Component → Django REST → Claude Agent SDK → Sefaria API
```

## System Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              Frontend                                    │
│  ┌─────────────────┐    ┌─────────────────┐    ┌──────────────────┐    │
│  │ LCChatbot.svelte│───▶│     api.js      │───▶│  SSE Streaming   │    │
│  │  (Web Component)│    │  (HTTP Client)  │    │ (Progress Events)│    │
│  └─────────────────┘    └─────────────────┘    └──────────────────┘    │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                    POST /api/v2/chat/stream
                                 │
┌────────────────────────────────▼────────────────────────────────────────┐
│                              Backend                                     │
│  ┌─────────────────┐    ┌─────────────────┐    ┌──────────────────┐    │
│  │    views.py     │───▶│  PromptService  │───▶│  Claude Agent SDK│    │
│  │  (Orchestrator) │    │(Braintrust+Cache│    │  (Tool Calling)  │    │
│  └────────┬────────┘    └────────┬────────┘    └────────▲─────────┘    │
│           │                      │                      │              │
│           │                      │                ┌─────┴─────┐       │
│           │                      │                │ ToolExecutor│      │
│           │                      │                │ (Sefaria API)│      │
│           │                      │                └─────────────┘      │
└─────────────────────────────────────────────────────────────────────────┘
```

## Agent (`server/chat/V2/agent/`)

The agent uses the Claude Agent SDK with MCP tools:

- Core prompt is loaded from Braintrust (with local fallback).
- The SDK manages tool calling and multi-step reasoning.
- Progress updates are emitted when tools start/end.

**Configuration:**
- Model: `claude-sonnet-4-5-20250929`
- Max tokens: 8000
- Temperature: 0.7

## Tools (`server/chat/V2/agent/tool_executor.py`)

Sefaria API wrapper providing text access:

| Tool | Description |
|------|-------------|
| `get_text` | Fetch text by reference (Genesis 1:1) |
| `text_search` | Full-text search across library |
| `english_semantic_search` | Semantic similarity search |
| `get_topic_details` | Topic information and links |
| `get_links_between_texts` | Cross-references between texts |
| `search_in_book` | Search within specific text |
| `search_in_dictionaries` | Dictionary/lexicon lookup |
| `get_current_calendar` | Jewish calendar info |
| `clarify_name_argument` | Autocomplete text names |
| `clarify_search_path_filter` | Validate book paths |

## Prompts (`server/chat/V2/prompts/`)

Single core prompt:

- **Core prompt** (`CORE_PROMPT_SLUG`) loaded from Braintrust (required)
- 5-minute in-memory cache to reduce external calls

## Data Models

```
ChatSession
├── session_id, user_id
├── conversation_summary
├── turn_count
├── total_input_tokens, total_output_tokens
└── total_cost_usd

ChatMessage
├── session (FK)
├── role (user | assistant)
├── content
├── tool_calls (JSON)
├── input_tokens, output_tokens
└── status (success | failed)

RouteDecision (legacy)
└── unused in V2
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v2/chat/stream` | POST | Send message (SSE streaming) |
| `/api/v2/chat/anthropic` | POST | Anthropic Messages API format (for Braintrust evals) |
| `/api/v2/chat/feedback` | POST | Feedback for Braintrust trace |
| `/api/v2/prompts/defaults` | GET | Default prompt slugs |
| `/api/history` | GET | Load conversation history |
| `/api/health` | GET | Health check |
| `/api/admin/reload-prompts` | POST | Invalidate prompt cache |

### POST /api/v2/chat/stream

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
  "toolCalls": [],
  "stats": {
    "llmCalls": 1,
    "toolCalls": 0,
    "latencyMs": 1200
  }
}
```

**SSE Events:**
- `progress` - Tool execution updates
- `message` - Final response
- `error` - Error details

## Observability

Braintrust Agent SDK tracing is enabled via `setup_claude_agent_sdk`, which captures:
- Agent calls
- Tool executions
- Latency and errors

## Directory Structure

```
server/
├── chat/
│   ├── views.py              # Shared endpoints (history, health)
│   ├── models.py             # Data models
│   ├── serializers.py        # Request/response validation
│   ├── auth/                 # Token authentication + Actor
│   ├── V2/
│   │   ├── agent/             # Claude Agent SDK integration
│   │   ├── guardrail/         # Pre-agent message filtering
│   │   ├── prompts/           # Braintrust prompt service
│   │   ├── logging/           # Turn logging (DB persistence)
│   │   ├── services/          # Session + shared chat ops
│   │   └── summarization/     # Conversation summary
└── chatbot_server/
    └── settings.py           # Django config

src/
├── components/
│   └── LCChatbot.svelte      # Main widget
├── lib/
│   ├── api.js                # HTTP client
│   ├── session.js            # Session management
│   ├── storage.js            # localStorage
│   └── markdown.js           # Rendering
└── main.js                   # Entry point
```

## Environment Variables

```bash
# Required
ANTHROPIC_API_KEY=sk-...
BRAINTRUST_API_KEY=...

# Optional
BRAINTRUST_PROJECT=...
BRAINTRUST_LOGGING_ENABLED=true  # set false to disable tracing (e.g. load tests); prompt fetching unaffected

# Optional - Prompts
CORE_PROMPT_SLUG=core-...

# Optional - Load Testing
MOCK_ANTHROPIC_URL=http://mock-anthropic:8002  # mock routes requests that pass isLoadTest:true in body

# Optional - Sefaria
SEFARIA_API_BASE_URL=https://www.sefaria.org
SEFARIA_AI_BASE_URL=https://ai.sefaria.org
SEFARIA_AI_TOKEN=...

# Database (production)
DB_HOST=...
DB_NAME=...
DB_USER=...
DB_PASSWORD=...
```

## Design Principles

1. **Single Core Prompt** - One Braintrust-managed system prompt for the agent.
2. **Tool-First Responses** - Prefer tools for sources and citations.
3. **Streaming Progress** - SSE keeps UI responsive during tool calls.
4. **Prompt Caching** - 5-minute TTL reduces external calls.
5. **Conversation Summarization** - Rolling summaries for token efficiency.
6. **Web Component** - Embeddable across any site.
