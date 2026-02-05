# Backend (Django)

Django REST API with Claude Agent SDK integration.

## Key Files

```
server/
├── chat/
│   ├── views.py                 # API endpoints
│   ├── models.py                # ChatSession, ChatMessage
│   ├── serializers.py           # Request/response validation
│   └── V2/
│       ├── views.py             # V2 streaming endpoints
│       ├── agent/
│       │   ├── claude_service.py    # Claude Agent SDK integration
│       │   ├── tool_executor.py     # Sefaria tool execution
│       │   ├── tool_schemas.py      # Tool definitions
│       │   └── sefaria_client.py    # Sefaria API client
│       ├── prompts/
│       │   ├── prompt_service.py    # Braintrust prompt loading
│       │   └── default_prompts.py   # Local fallbacks
│       └── summarization/
│           └── summary_service.py   # Conversation summarization
└── chatbot_server/
    └── settings.py              # Django config
```

## Architecture

- **Claude Agent SDK** for tool calling and multi-step reasoning
- **Braintrust** for prompt management (with local fallbacks)
- **SSE streaming** for real-time progress updates
- **Conversation summarization** for token efficiency

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v2/chat/stream` | POST | Send message (SSE streaming) |
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
| `BRAINTRUST_API_KEY` | No | Prompt management |
| `BRAINTRUST_PROJECT` | No | Braintrust project name |
| `DB_HOST`, `DB_NAME`, etc. | No | PostgreSQL (SQLite default) |
