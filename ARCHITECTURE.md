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
│  │ LCChatbot.svelte│───▶│     api.js      │───▶│   WebSocket     │    │
│  │  (Web Component)│    │  (HTTP Client)  │    │ (Progress Events)│    │
│  └─────────────────┘    └─────────────────┘    └──────────────────┘    │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                    WebSocket /api/ws/v2/chat
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

- **Core prompt** (`CORE_PROMPT_SLUG`) loaded from Braintrust
- Local fallback in `default_prompts.py`
- 5-minute cache to reduce external calls

## Data Models

```
ChatSession
├── session_id, user_id
├── conversation_summary
├── turn_count
└── total_input_tokens, total_output_tokens

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
| `/api/ws/v2/chat` | WebSocket | Send message (progress + response) |
| `/api/v2/chat/feedback` | POST | Feedback for Braintrust trace |
| `/api/v2/prompts/defaults` | GET | Default prompt slugs |
| `/api/history` | GET | Load conversation history |
| `/api/health` | GET | Health check |
| `/api/admin/reload-prompts` | POST | Invalidate prompt cache |

**WebSocket Events:**
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
│   ├── views.py              # API orchestration
│   ├── models.py             # Data models
│   ├── serializers.py        # Request/response validation
│   ├── V2/
│   │   ├── agent/             # Claude Agent SDK integration
│   │   ├── prompts/           # Braintrust prompt service + fallbacks
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

# Optional - Observability
BRAINTRUST_API_KEY=...
BRAINTRUST_PROJECT=...

# Optional - Prompts
CORE_PROMPT_SLUG=core-...

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
3. **Streaming Progress** - WebSocket updates keep UI responsive during tool calls.
4. **Prompt Caching** - 5-minute TTL reduces external calls.
5. **Conversation Summarization** - Rolling summaries for token efficiency.
6. **Web Component** - Embeddable across any site.
