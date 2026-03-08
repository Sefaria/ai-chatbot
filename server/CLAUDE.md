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
| `AGENT_MODEL` | No | Model for normal requests (default: claude-sonnet-4-5-20250929) |
| `LOAD_TEST_MODEL` | No | Model for load test requests (default: claude-haiku-4-5-20251001) |
| `CHATBOT_USER_TOKEN_SECRET` | No | AES-GCM key for userId tokens (default: `secret`) |
| `DB_HOST`, `DB_NAME`, etc. | No | PostgreSQL (SQLite default) |

## Load Testing

The `isLoadTest` boolean field on `POST /api/v2/chat/stream` enables a cost-optimised path:

| Behaviour | Normal (`false`) | Load test (`true`) |
|-----------|------------------|--------------------|
| Model | `AGENT_MODEL` (Sonnet) | `LOAD_TEST_MODEL` (Haiku) |
| Braintrust tracing | Enabled | Disabled (noop — init_logger never called) |
| SDK subprocess env | Includes `BRAINTRUST_API_KEY` | Omits Braintrust keys |
| Thread executor | `TracedThreadPoolExecutor` | Plain `ThreadPoolExecutor` |

Run the load test script against Docker Compose:

```bash
# Start the stack (reads ANTHROPIC_API_KEY from server/.env)
docker compose up --build

# Run 5 concurrent users, 20 total requests
cd server
source venv/bin/activate
python -m loadtest.load_test --url http://localhost:8001 --users 5 --requests 20

# Single verbose request to inspect SSE events
python -m loadtest.load_test --url http://localhost:8001 --users 1 --requests 1 --verbose

# Normal (non-load-test) request for comparison
python -m loadtest.load_test --url http://localhost:8001 --users 1 --requests 1 --no-load-test --timeout 300
```

The script auto-generates valid encrypted user tokens using `CHATBOT_USER_TOKEN_SECRET` (defaults to `secret`, matching the Docker Compose default).
