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
│       │   ├── tracing_guard.py     # Thread-local Braintrust span suppression
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
├── chatbot_server/
│   └── settings.py              # Django config
└── loadtest/
    └── load_test.py             # Concurrent SSE load test script
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
| `LOAD_TEST_MODEL` | No | Model for load test requests (default: claude-haiku-4-5-20251001) |
| `CHATBOT_USER_TOKEN_SECRET` | Outside debug | AES-GCM key for userId tokens (default `secret` only when `DJANGO_DEBUG=True`; startup fails with the default or empty value otherwise) |
| `DJANGO_DEBUG` | No | Default `False`; set `True` for local development |
| `DJANGO_SECRET_KEY` | Outside debug | Django secret; startup fails with the dev placeholder otherwise |
| `DB_HOST`, `DB_NAME`, etc. | No | PostgreSQL (SQLite default) |
| `MCP_TOKEN_SECRET` | No | Shared with Sefaria for MCP sign-in (`mcp-login` and `mcp` tokens); unset disables `/mcp/user` and `/mcp/admin` |
| `MCP_OAUTH_SIGNING_KEY` | No | Signs `/mcp/user` and `/mcp/admin` access-token JWTs; shared by Django and the MCP process |
| `MCP_OAUTH_ISSUER` | No | OAuth issuer / chat host (default `https://chat.sefaria.org`) |
| `MCP_USER_RESOURCE_URL` | No | Default `<issuer>/mcp/user` |
| `MCP_ADMIN_RESOURCE_URL` | No | Staff topic-prompt tools; default beside `MCP_USER_RESOURCE_URL` (`.../mcp/admin`) |
| `SEFARIA_MCP_AUTHORIZE_URL` | No | Sefaria sign-in handoff (default `https://www.sefaria.org/api/mcp/authorize`) |

## Load Testing

The `isLoadTest` boolean field on `POST /api/v2/chat/stream` enables a cost-optimised path:

| Behaviour | Normal (`false`) | Load test (`true`) |
|-----------|------------------|--------------------|
| Model | `AGENT_MODEL` (Sonnet) | `LOAD_TEST_MODEL` (Haiku) |
| Braintrust tracing | Full (SDK spans + manual spans) | Suppressed via `tracing_guard.py` |
| SDK subprocess env | Includes `BRAINTRUST_API_KEY` | Omits Braintrust keys |
| Thread executor | `TracedThreadPoolExecutor` | Plain `ThreadPoolExecutor` |
| Span creation | `start_span` creates real spans | `start_span` returns `NOOP_SPAN` |

`setup_claude_agent_sdk` patches the SDK globally (once per process). To prevent
load-test spans from leaking, `tracing_guard.py` intercepts `start_span` with a
thread-local flag — load-test threads run inside `suppress_tracing()` so every
`start_span` call returns `NOOP_SPAN`.

Run the load test script against Docker Compose:

```bash
# Start the local stack (docker-compose.local.yml sets DJANGO_DEBUG=true for the dev secret fallbacks;
# any other compose file must supply DJANGO_SECRET_KEY and CHATBOT_USER_TOKEN_SECRET)
docker compose -f docker-compose.local.yml up --build

# Run 5 concurrent users, 20 total requests
cd server
source venv/bin/activate
python -m loadtest.load_test --url http://localhost:8001 --users 5 --requests 20

# Single verbose request to inspect SSE events
python -m loadtest.load_test --url http://localhost:8001 --users 1 --requests 1 --verbose

# Normal (non-load-test) request for comparison
python -m loadtest.load_test --url http://localhost:8001 --users 1 --requests 1 --no-load-test --timeout 300
```

The script auto-generates valid encrypted user tokens using `CHATBOT_USER_TOKEN_SECRET`. The server reads it from settings, so with debug off the value must be set to a real secret, and the load test must use the same value.
