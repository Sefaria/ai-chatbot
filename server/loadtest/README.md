# Load Testing

Tools for load testing the chatbot server without incurring Claude API costs.

## Architecture

```
Load Test Script (httpx)  →  Django Server (:8001)  →  Mock Anthropic (:8002)
                                    ↓
                              Real Sefaria API
                              Real DB writes
                              Real SSE streaming
```

The mock replaces **only** the Anthropic API. Everything else runs for real:
ClaudeAgentService, tool executor, Sefaria API calls, DB persistence, SSE streaming.

## Quick Start (Docker Compose)

The easiest way to run the full load-test stack (PostgreSQL + mock Anthropic + Django app):

```bash
# 1. Build and start all services
docker compose up -d --build

# 2. Run database migrations (first time only)
docker compose exec app python manage.py migrate

# 3. Run the load test against the app container
cd server
source venv/bin/activate
pip install -r loadtest/requirements.txt
python -m loadtest.load_test --url http://localhost:8001 -n 50 -c 10

# 4. Tear down when done
docker compose down
```

This starts three services:
- **postgres** (:5438) — database
- **mock-anthropic** (:8002) — mock Anthropic API
- **app** (:8001) — Django server with `ANTHROPIC_BASE_URL` pointed at the mock

## Quick Start (Manual)

If you prefer to run services individually:

```bash
# 1. Install dependencies
source server/venv/bin/activate
pip install -r server/loadtest/requirements.txt

# 2. Start the mock Anthropic server
uvicorn loadtest.mock_anthropic:app --port 8002

# 3. Start the Django server pointing at the mock
ANTHROPIC_BASE_URL=http://localhost:8002 python manage.py runserver 0.0.0.0:8001

# 4. Run the load test
python -m loadtest.load_test --url http://localhost:8001 -n 50 -c 10
```

## Mock Anthropic Server

`mock_anthropic.py` mimics the Anthropic Messages API streaming format:

- Streams SSE events in the exact Anthropic wire format
- Simulates tool-calling loops (configurable number of rounds)
- Handles guardrail requests (Haiku model → fast "allowed" response)
- Streams tokens with configurable delay (default ~5-15s total per request)
- Runtime-configurable via PUT `/config`

### Configuration

Adjust mock behaviour at runtime:

```bash
# Increase tool calls per turn
curl -X PUT http://localhost:8002/config \
  -H "Content-Type: application/json" \
  -d '{"tool_calls_per_turn": 3}'

# Slow down token streaming (simulate slower model)
curl -X PUT http://localhost:8002/config \
  -H "Content-Type: application/json" \
  -d '{"token_delay": 0.15}'

# View current config
curl http://localhost:8002/config
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `tool_calls_per_turn` | 2 | Number of tool-calling rounds per request |
| `token_delay` | 0.10 | Seconds between streamed tokens (targets ~5-15s total flow) |
| `token_jitter` | 0.05 | Random ± jitter on token delay |
| `response_fragments` | 6 | Number of text fragments in final response |
| `input_tokens` | 3500 | Simulated input token count |

## Load Test Script

`load_test.py` sends concurrent requests and measures performance:

```bash
# Basic test
python -m loadtest.load_test --url http://localhost:8001 -n 50 -c 10

# Stress test
python -m loadtest.load_test --url http://localhost:8001 -n 200 -c 50

# With ramp-up (spread requests over 30 seconds)
python -m loadtest.load_test --url http://localhost:8001 -n 100 -c 20 --ramp-up 30

# Save results to JSON
python -m loadtest.load_test --url http://localhost:8001 -n 50 -c 10 --json-output results.json
```

### Metrics Reported

- **TTFB** (Time to First Byte): p50, p90, p95, p99, min, max
- **Total Response Time**: p50, p90, p95, p99, min, max
- **Error Rate**: percentage of failed requests
- **Throughput**: requests per second

## Testing Tiers

| Tier | What | Cost | When |
|------|------|------|------|
| **1. Mock** | Server capacity (threads, memory, DB) | $0 | Daily during dev |
| **2. Haiku** | End-to-end with cheap model | ~$15 | Before milestones |
| **3. Sonnet** | Production validation | ~$10 | Final sign-off |

### Tier 2: Using Claude Haiku

```bash
# Override model in Django settings or environment
AGENT_MODEL=claude-3-5-haiku-20241022 python manage.py runserver 0.0.0.0:8001
```

## Tests

```bash
cd server
source venv/bin/activate
pytest loadtest/test_mock_anthropic.py -v
```
