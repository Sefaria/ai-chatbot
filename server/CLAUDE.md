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
│       │   ├── sdk_options_builder.py # Claude SDK subprocess options
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
| `BRAINTRUST_API_KEY` | Yes | Prompt management & tracing |
| `BRAINTRUST_PROJECT` | No | Braintrust project name |
| `AGENT_MODEL` | No | Model for normal requests (default: claude-sonnet-4-6) |
| `APPETIZER_MODEL` | No | Model for the topic appetizer (default: same as `AGENT_MODEL`) |
| `CHATBOT_USER_TOKEN_SECRET` | No | AES-GCM key for userId tokens (default: `secret`) |
| `DB_HOST`, `DB_NAME`, etc. | No | PostgreSQL (SQLite default) |
| `MCP_TOKEN_SECRET` | No | Shared with Sefaria for MCP sign-in (`mcp-login` and `mcp` tokens); unset disables `/mcp/user` and `/mcp/admin` |
| `MCP_OAUTH_SIGNING_KEY` | No | Signs `/mcp/user` and `/mcp/admin` access-token JWTs; shared by Django and the MCP process |
| `MCP_OAUTH_ISSUER` | No | OAuth issuer / chat host (default `https://chat.sefaria.org`) |
| `MCP_USER_RESOURCE_URL` | No | Default `<issuer>/mcp/user` |
| `MCP_ADMIN_RESOURCE_URL` | No | Staff topic-prompt tools; default beside `MCP_USER_RESOURCE_URL` (`.../mcp/admin`) |
| `SEFARIA_MCP_AUTHORIZE_URL` | No | Sefaria sign-in handoff (default `https://www.sefaria.org/api/mcp/authorize`) |
