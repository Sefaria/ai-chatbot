# Backend (Django)

Django REST API with Claude Agent SDK integration.

## Key Files

```
server/
├── chat/
│   ├── views.py                 # Shared endpoints (history, health)
│   ├── models.py                # ChatSession, ChatMessage
│   ├── serializers.py           # Request/response validation
│   ├── auth/
│   │   ├── auth_service.py      # Token authentication
│   │   └── actor.py             # Actor (authenticated user)
│   └── V2/
│       ├── views.py             # V2 streaming endpoints
│       ├── anthropic_views.py   # Anthropic Messages API endpoint
│       ├── utils.py             # Shared helpers (clients, config)
│       ├── agent/
│       │   ├── claude_service.py    # Claude Agent SDK integration
│       │   ├── tool_executor.py     # Sefaria tool execution
│       │   ├── tool_schemas.py      # Tool definitions
│       │   └── sefaria_client.py    # Sefaria API client
│       ├── guardrail/
│       │   └── guardrail_service.py # Pre-agent message filtering
│       ├── prompts/
│       │   ├── prompt_service.py    # Braintrust prompt loading
│       │   └── prompt_fragments.py  # LLM-facing text fragments
│       ├── logging/
│       │   └── turn_logging_service.py  # DB persistence per turn
│       ├── services/
│       │   ├── chat_service.py      # Shared chat operations
│       │   └── session_service.py   # Session management
│       └── summarization/
│           └── summary_service.py   # Conversation summarization
└── chatbot_server/
    └── settings.py              # Django config
```

## Architecture

- **Claude Agent SDK** for tool calling and multi-step reasoning
- **Braintrust** for prompt management and tracing (required)
- **SSE streaming** for real-time progress updates
- **Conversation summarization** for token efficiency

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v2/chat/stream` | POST | Send message (SSE streaming) |
| `/api/v2/chat/anthropic` | POST | Anthropic Messages API format (for Braintrust) |
| `/api/v2/chat/feedback` | POST | Feedback for trace |
| `/api/v2/prompts/defaults` | GET | Default prompt slugs |
| `/api/history` | GET | Conversation history |
| `/api/health` | GET | Health check |

## Commands

```bash
python manage.py runserver 0.0.0.0:8001  # Start server
python manage.py migrate                  # Run migrations
pytest                                    # Run tests
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `BRAINTRUST_API_KEY` | When enabled | Prompt management & tracing |
| `BRAINTRUST_PROJECT` | No | Braintrust project name |
| `BRAINTRUST_LOGGING_ENABLED` | No | `true` (default) or `false` to disable tracing for load tests |
| `IS_LOAD_TESTING` | No | `false` (default) or `true` to route requests to mock Anthropic server |
| `MOCK_ANTHROPIC_URL` | No | Mock server URL (default: `http://mock-anthropic:8002`) |
| `DB_HOST`, `DB_NAME`, etc. | No | PostgreSQL (SQLite default) |
