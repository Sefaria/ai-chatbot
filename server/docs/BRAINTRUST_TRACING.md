# Braintrust Tracing - Current State

This document describes the current implementation of Braintrust tracing in the v2 API.

## Overview

The v2 API uses [Braintrust](https://www.braintrust.dev/docs/guides/traces) for observability. Braintrust's tracing model consists of:

- **Traces**: Represent a single request/interaction
- **Spans**: Units of work within a trace (LLM calls, tool executions, etc.)

Spans can nest to show execution hierarchy. Each span can log input, output, metadata, metrics, and scores.

## Current Implementation

### Initialization

**File:** `server/chat/V2/agent/claude_service.py` (lines 162-184)

```python
def _setup_braintrust_tracing(self) -> None:
    bt_api_key = os.environ.get("BRAINTRUST_API_KEY")
    bt_project = os.environ.get("BRAINTRUST_PROJECT", "On Site Agent")

    global _BRAINTRUST_SETUP_DONE
    if _BRAINTRUST_SETUP_DONE:
        self._braintrust_enabled = True
        return

    setup_claude_agent_sdk(project=bt_project, api_key=bt_api_key)
    self._braintrust_enabled = True
    _BRAINTRUST_SETUP_DONE = True
```

**Key design decision:** Uses a global flag `_BRAINTRUST_SETUP_DONE` (not thread-local) because `setup_claude_agent_sdk()` patches SDK classes globally. Calling it multiple times creates deeply nested duplicate spans.

### Top-Level Span

**File:** `server/chat/V2/agent/claude_service.py` (lines 215-219)

The agent wraps execution with `@traced`:

```python
async def send_message(...) -> AgentResponse:
    self._setup_braintrust_tracing()

    async def run() -> AgentResponse:
        return await self._send_message_inner(...)

    if braintrust and self._braintrust_enabled:
        traced_run = braintrust.traced(name="chat-agent", type="llm")(run)
        return await traced_run()

    return await run()
```

Creates a top-level span named `"chat-agent"` with type `"llm"`.

### What's Currently Logged

**Explicit logging (in `_send_message_inner`):**

| Field | Type | Description |
|-------|------|-------------|
| `input.message` | input | Last user message |
| `input.page_url` | input | Page URL if provided |
| `input.summary` | input | Conversation summary if provided |
| `metadata.core_prompt_id` | metadata | Braintrust prompt slug |
| `metadata.core_prompt_version` | metadata | Prompt version number |
| `metadata.core_prompt_in_options` | metadata | Whether prompt was in SDK options |
| `metadata.summary_included` | metadata | Whether conversation summary was included |
| `metadata.model` | metadata | Claude model used |
| `metadata.session_id` | metadata | Session ID (when provided) |
| `metrics.latency_ms` | metrics | Response latency in milliseconds |
| `metrics.tool_count` | metrics | Number of tool invocations |

**Auto-captured by `@traced` decorator:**

The `@traced` wrapper captures the `AgentResponse` return value as span output. This includes `content`, `tool_calls`, `llm_calls`, `latency_ms`, `model`, and `trace_id`. Do not also set `output` explicitly — it will be overwritten.

**On error:** Error status and message are logged to the span before re-raising.

### What's NOT Logged to Braintrust

| Data | Where it lives | Notes |
|------|----------------|-------|
| `llm_calls` | `AgentResponse` (always None) | Can't count SDK-internal LLM calls |
| Token counts | Nowhere | Not captured yet (Phase 3) |
| Prompt cache/fetch | Python logs (`chat.prompts`) | Deliberately not traced — keep spans simple |

### Span Hierarchy

Confirmed structure from real trace analysis (trace_6614a94e):

```
chat-agent (root, our @traced span — type: llm)
└── Claude Agent (SDK task span — type: task)
    └── [intermediate span]
        ├── anthropic.messages.create ×N (LLM calls)
        ├── text_search ×N
        ├── get_text ×N
        └── english_semantic_search ×N
```

SDK tool spans log input, output (`{content, is_error}`), and start/end timestamps. SDK LLM spans log full message arrays and responses. We don't create our own child spans — the SDK's are adequate.

## V2 API Endpoints

### Streaming Endpoint (`/api/v2/chat/stream`)

**File:** `server/chat/V2/views.py`

Flow:
1. Authenticate request
2. Create/get session
3. Load conversation summary
4. Save user message to DB
5. **Run agent in `TracedThreadPoolExecutor`** (preserves span context across threads)
6. Log response to DB via `finalize_success()`
7. Stream SSE events with `traceId` in final response

### Anthropic-Compatible Endpoint (`/api/v2/chat/anthropic`)

**File:** `server/chat/V2/anthropic_views.py`

Flow:
1. Deserialize Anthropic Messages API format
2. Authenticate via `X-Api-Key` header
3. Create ephemeral or multi-turn session (via `X-Session-ID` header)
4. **Run agent** (automatic @traced wrapper)
5. Log response to DB
6. Return Anthropic Messages API format with metadata:
   - `trace_id` from agent response
   - `origin: "braintrust"`
   - `stats` (llmCalls, toolCalls, latencyMs)

### Trace ID Propagation

Trace ID is obtained from (in order):
1. `ClaudeSDKClient.trace_id` or `last_trace_id` attribute
2. Current Braintrust span's `id` (fallback)

Included in responses:
- Streaming: `traceId` in SSE events
- Anthropic: `metadata.trace_id` in response body

## Feedback Logging

**Endpoint:** `/api/v2/chat/feedback`

Uses `braintrust.init_logger()` to log user feedback:
- Associates feedback with `trace_id` from the message
- Logs score, comment, and metadata

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BRAINTRUST_API_KEY` | (required) | API key to enable tracing |
| `BRAINTRUST_PROJECT` | `"On Site Agent"` | Project name in Braintrust |
| `CORE_PROMPT_SLUG` | `"core-8fbc"` | Prompt ID for system prompt |

## Files

| File | Purpose |
|------|---------|
| `server/chat/V2/agent/claude_service.py` | Main agent with @traced wrapper |
| `server/chat/V2/views.py` | Streaming endpoint with TracedThreadPoolExecutor |
| `server/chat/V2/anthropic_views.py` | Anthropic endpoint with trace_id |
| `server/chat/V2/logging/turn_logging_service.py` | DB logging of stats |
| `server/chat/V2/prompts/prompt_service.py` | Braintrust prompt fetching |
| `server/chat/tests/test_braintrust_tracing.py` | Tests for single global setup |

## Braintrust SDK Reference

Key APIs used:

```python
# Initialize SDK wrapper (once per process)
from braintrust.wrappers.claude_agent import setup_claude_agent_sdk
setup_claude_agent_sdk(project="...", api_key="...")

# Wrap function with tracing
from braintrust import traced
@traced(name="span-name", type="llm")
async def my_function(): ...

# Log to current span
from braintrust import current_span
span = current_span()
span.log(input=..., output=..., metadata=..., metrics=...)

# Initialize logger for feedback
from braintrust import init_logger
logger = init_logger(project="...")
logger.log_feedback(id=trace_id, scores=..., metadata=...)
```

See [Braintrust Tracing Docs](https://www.braintrust.dev/docs/guides/traces) and [Write Logs Guide](https://www.braintrust.dev/docs/guides/logs/write) for full API reference.
