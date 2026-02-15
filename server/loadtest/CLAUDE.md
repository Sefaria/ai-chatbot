# Load Testing

Tools for measuring server capacity without Claude API costs.

## Files

- `mock_anthropic.py` — FastAPI server mimicking Anthropic Messages API (SSE streaming + tool-calling loops)
- `load_test.py` — Async httpx script for hitting `/api/v2/chat/stream` with configurable concurrency
- `test_mock_anthropic.py` — Tests verifying mock SSE format correctness

## Usage

```bash
# 1. Start mock (Terminal 1)
uvicorn loadtest.mock_anthropic:app --port 8002

# 2. Start Django pointed at mock (Terminal 2)
ANTHROPIC_BASE_URL=http://localhost:8002 python manage.py runserver 0.0.0.0:8001

# 3. Run load test (Terminal 3)
python -m loadtest.load_test --url http://localhost:8001 -n 50 -c 10
```

## How It Works

The mock intercepts all Anthropic API calls. Everything else runs for real:
ClaudeAgentService, Sefaria tools, DB writes, SSE streaming, Braintrust tracing.

- First request per turn → mock returns `tool_use` block (triggers real tool executor)
- After N rounds → mock returns text response
- Haiku model requests → instant `{"allowed": true}` (guardrail path)

## Tests

```bash
pytest loadtest/test_mock_anthropic.py -v
```
