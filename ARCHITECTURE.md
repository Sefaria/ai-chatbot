# Architecture

## Overview

LC Chatbot is an embeddable AI assistant for Jewish text learning. It uses a **routed orchestration pattern**: user messages flow through classification, prompt/tool selection, and Claude with Sefaria API access.

```
Svelte Web Component → Django REST → Router → Claude Agent → Sefaria API
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
│  │    views.py     │───▶│  RouterService  │───▶│ GuardrailChecker │    │
│  │  (Orchestrator) │    │(Flow Classifier)│    │ (Safety Patterns)│    │
│  └────────┬────────┘    └────────┬────────┘    └──────────────────┘    │
│           │                      │                                      │
│           │              RouteResult                                    │
│           │     (flow, prompts, tools, safety)                         │
│           │                      │                                      │
│           ▼                      ▼                                      │
│  ┌─────────────────┐    ┌─────────────────┐    ┌──────────────────┐    │
│  │  AgentService   │◀───│  PromptService  │    │  ToolExecutor    │    │
│  │  (Claude Loop)  │    │(Braintrust+Cache│    │  (Sefaria APIs)  │    │
│  └────────┬────────┘    └─────────────────┘    └────────▲─────────┘    │
│           │                                             │               │
│           └─────────────────────────────────────────────┘               │
│                          Tool Calls                                     │
└─────────────────────────────────────────────────────────────────────────┘
```

## Flows

Four routing flows determine behavior:

| Flow | Description | Tools | Use Case |
|------|-------------|-------|----------|
| `TRANSLATION` | Translation requests | 2 | "Translate this verse", "What does X mean in English?" |
| `DISCOVERY` | Source/text finding | 14 | "Find sources about...", "Where is it written...?" |
| `DEEP_ENGAGEMENT` | Learning/understanding | 14 | "Explain the concept of...", "Go deep on this text..." |
| `REFUSE` | Blocked content | 0 | Safety guardrails triggered |

## Components

### Router (`server/chat/V2/router/`)

Classifies intent and selects resources per turn.

```python
RouteResult:
  flow: Flow              # TRANSLATION | DISCOVERY | DEEP_ENGAGEMENT | REFUSE
  confidence: float       # 0.0 - 1.0
  reason_codes: List      # ROUTE_TRANSLATION_KEYWORDS, GUARDRAIL_*, etc.
  prompt_bundle: Bundle   # core + flow-specific prompts
  tools: List[str]        # tool names for this flow
  session_action: Action  # CONTINUE | SWITCH_FLOW | END
  safety: SafetyResult    # allowed flag + refusal message
```

**Classification Methods:**
- **AI-based** (default): Claude Haiku with Braintrust prompts
- **Rule-based** (fallback): Keyword pattern matching

**Guardrails** check for:
- Prompt injection attempts
- Harassment/hate speech
- High-risk halachic topics (e.g., life-threatening situations)

### Agent (`server/chat/V2/agent/`)

Executes Claude with tools in an agentic loop.

```
Loop (max 10 iterations):
  1. Call Claude with system prompt + conversation + tools
  2. Parse response (text + tool_use blocks)
  3. If tools requested:
     - Execute each via SefariaToolExecutor
     - Add results to conversation
     - Continue loop
  4. If no tools: return final response
```

**Configuration:**
- Model: `claude-sonnet-4-5-20250929`
- Max tokens: 8000
- Temperature: 0.7

### Tools (`server/chat/V2/agent/tool_executor.py`)

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

### Prompts (`server/chat/V2/prompts/`)

Two-layer prompt system:
1. **Core prompt** (`core-8fbc`): System-wide instructions
2. **Flow prompt**: Flow-specific guidance

Fetched from Braintrust with 5-minute cache, falls back to local defaults.

### Frontend (`src/`)

Svelte 5 Web Component (`<lc-chatbot>`):

```html
<lc-chatbot
  user-id="user123"
  api-base-url="https://api.example.com"
  default-open="false"
  placement="bottom-right">
</lc-chatbot>
```

**Features:**
- SSE streaming with progress events
- Resizable panel (drag edges)
- Infinite scroll history
- Draft persistence

**Libraries:**
- `api.js` - HTTP client with streaming
- `session.js` - Session ID management
- `storage.js` - localStorage persistence
- `markdown.js` - Response rendering

## Data Models

```
ChatSession
├── session_id, user_id
├── current_flow
├── conversation_summary
├── turn_count
└── total_input_tokens, total_output_tokens

ChatMessage
├── session (FK)
├── role (user | assistant)
├── content
├── route_decision (FK)
├── tool_calls (JSON)
├── input_tokens, output_tokens
└── status (success | refused | error)

RouteDecision
├── session, turn_id
├── flow, confidence
├── reason_codes (JSON)
├── prompt_bundle (JSON)
├── tools_attached (JSON)
└── guardrails_triggered (JSON)
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/chat/stream` | POST | Send message (SSE streaming) |
| `/api/chat` | POST | Send message (non-streaming) |
| `/api/history` | GET | Load conversation history |
| `/api/health` | GET | Health check |

**Request:**
```json
{
  "userId": "string",
  "sessionId": "string",
  "messageId": "string",
  "timestamp": "ISO8601",
  "text": "string",
  "context": {
    "pageUrl": "string",
    "locale": "string"
  }
}
```

**SSE Events:**
- `routing` - Flow decision
- `progress` - Tool execution updates
- `message` - Final response
- `error` - Error details

## Observability

**Braintrust Integration:**
- Native tracing via `@traced` decorators
- Logs: input, prompts, tools, output, metrics
- Nested spans for tool execution

**Metrics Tracked:**
- Input/output tokens
- Cache tokens (creation + read)
- Tool calls (count, latency, errors)
- Router latency
- Total latency

## Directory Structure

```
server/
├── chat/
│   ├── views.py              # API orchestration
│   ├── models.py             # Data models
│   ├── serializers.py        # Request/response validation
│   ├── router/
│   │   ├── router_service.py # Flow classification
│   │   ├── ai_router.py      # Claude-based classifier
│   │   ├── guardrails.py     # Safety pattern matching
│   │   └── reason_codes.py   # Decision audit codes
│   ├── agent/
│   │   ├── claude_service.py # Agent loop
│   │   ├── tool_executor.py  # Tool dispatch
│   │   └── sefaria_client.py # Sefaria API client
│   ├── prompts/
│   │   ├── prompt_service.py # Braintrust + cache
│   │   └── defaults.py       # Local fallbacks
│   └── tests/                # 255 tests
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
LANGSMITH_API_KEY=...

# Optional - Sefaria
SEFARIA_API_BASE_URL=https://www.sefaria.org
SEFARIA_AI_BASE_URL=https://ai.sefaria.org
SEFARIA_AI_TOKEN=...

# Optional - Features
ROUTER_USE_AI=true
GUARDRAILS_USE_AI=true

# Database (production)
DB_HOST=...
DB_NAME=...
DB_USER=...
DB_PASSWORD=...
```

## Design Principles

1. **Routed Orchestration** - Router classifies, agent executes
2. **Flow-Based Tool Selection** - Reduce tokens by limiting tools per flow
3. **Streaming Progress** - SSE keeps UI responsive during long operations
4. **Fallback Patterns** - AI classification falls back to rule-based
5. **Prompt Caching** - 5-minute TTL reduces external calls
6. **Conversation Summarization** - Rolling summaries for token efficiency
7. **Web Component** - Embeddable across any site
