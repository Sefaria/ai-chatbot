# Braintrust Logging Structure

> **Status:** IMPLEMENTED
> **Last Updated:** January 2025

---

## Summary

Hierarchical trace logging for cost visibility, evaluations, and debugging. All LLM calls are traced with token metrics using Braintrust-compatible field names.

**Trace Hierarchy:**
```
request (views.py) - type: task
├── router (router_service.py) - type: function
│   ├── guardrails-llm (ai_guardrails.py) - type: llm
│   │   └── Token metrics for safety check
│   └── flow-classifier-llm (ai_router.py) - type: llm
│       └── Token metrics for intent classification
│
├── chat-agent (claude_service.py) - type: llm
│   ├── llm-call-1 (individual Claude API call)
│   ├── tool:get_text
│   ├── tool:text_search
│   └── llm-call-2 (with tool results)
│
└── summary-llm (summary_service.py) - type: llm
    └── Token metrics for conversation summarization
```

**Benefits:**
- See routing decisions at a glance (not buried in metadata)
- Debug individual LLM calls within the agent loop
- Track token usage per call for accurate cost estimation
- Request span shows **total** tokens across all LLM calls
- Create eval datasets from production logs
- Score citation accuracy via `refs` field

---

## Token Metrics Format

Token metrics use Braintrust-compatible (OpenAI-style) field names for cost calculation:

| Anthropic API | Braintrust Format |
|---------------|-------------------|
| `input_tokens` | `prompt_tokens` |
| `output_tokens` | `completion_tokens` |
| `cache_creation_input_tokens` | `prompt_cache_creation_tokens` |
| `cache_read_input_tokens` | `prompt_cached_tokens` |
| (calculated) | `tokens` (total) |

This conversion is handled by `TokenUsage.to_braintrust()` in `chat/metrics.py`.

---

## Key Metadata Fields

| Field | Values | How Set |
|-------|--------|---------|
| `environment` | `dev` / `staging` / `prod` | `ENVIRONMENT` env var (defaults to `"dev"`) |
| `source` | `component` / `api` | Inferred: `clientVersion` present → `component`, else `api` |
| `page_type` | `home` / `reader` / `eval` / `staging` / `other` | Parsed from `pageUrl` |
| `site` | e.g., `www.sefaria.org` | Parsed from `pageUrl` |

**Local development** sends `environment: "dev"` and `source: "component"` (when using the widget).

---

## Span Details

### 1. Request Span (`orchestrator.py`)

Top-level span wrapping the entire request. Aggregates token usage from ALL LLM calls.

```python
from chat.observability import create_span

request_span = create_span(name="request", type="task")
    request_span.log(
        input={"query": user_message},
        metadata={
            "session_id": "...",
            "user_id": "...",
            "turn_id": "...",
            "site": "www.sefaria.org",
            "page_type": "reader",
            "page_url": "...",
            "client_version": "1.0.0",
            "source": "component",  # or "api" for direct API calls
        },
        tags=[environment],  # dev | staging | prod
    )
    # ... router, agent, and summary calls ...
    request_span.log(
        output={"response": "..."},
        tags=[flow],  # search | halachic | general
        metrics={
            "latency_ms": ...,
            "llm_calls": ...,  # Total: agent + router (2) + summary (1)
            "tool_calls": ...,
            # Aggregated from ALL LLM calls (router + agent + summary)
            "prompt_tokens": ...,
            "completion_tokens": ...,
            "prompt_cache_creation_tokens": ...,
            "prompt_cached_tokens": ...,
            "tokens": ...,  # Total
        },
    )
```

### 2. Router Span (`router_service.py`)

Captures the routing decision with full context. Contains nested LLM spans.

```python
from chat.observability import traced, current_span

@traced(name="router", type="function")
def route(...):
    span = current_span()
    span.log(
        input={
            "query": user_message,
            "conversation_summary": "...",
            "previous_flow": "GENERAL",
        },
    )
    # ... guardrails-llm and flow-classifier-llm spans ...
    span.log(
        output={
            "flow": "HALACHIC",
            "confidence": 0.92,
            "decision_id": "dec_abc123",
            "reason_codes": ["ROUTE_HALACHIC_KEYWORDS", ...],
            "tools": ["get_text", "text_search", ...],
        },
        metadata={
            "decision_id": "dec_abc123",
            "session_action": "CONTINUE",
            "core_prompt_id": "core-8fbc",
            "flow_prompt_id": "bt_prompt_halachic",
            "classifier_type": "ai",  # or "rule"
        },
        metrics={
            "latency_ms": ...,
            "confidence": 0.92,
        },
    )
```

### 3. Guardrails LLM Span (`ai_guardrails.py`)

Individual LLM call for content safety check. Nested under router span.

```python
from chat.observability import start_span

with start_span(name="guardrails-llm", type="llm") as span:
    span.log(
        input={"message": message[:500]},
        metadata={"model": "claude-3-5-haiku-20241022"},
    )
    response = client.messages.create(...)
    usage = TokenUsage.from_anthropic(response.usage)
    span.log(
        output={"decision": response.content[0].text[:200]},
        metrics={"latency_ms": ..., **usage.to_braintrust()},
    )
```

### 4. Flow Classifier LLM Span (`ai_router.py`)

Individual LLM call for intent classification. Nested under router span.

```python
from chat.observability import start_span

with start_span(name="flow-classifier-llm", type="llm") as span:
    span.log(
        input={"message": message[:500], "previous_flow": previous_flow},
        metadata={"model": "claude-3-5-haiku-20241022"},
    )
    response = client.messages.create(...)
    usage = TokenUsage.from_anthropic(response.usage)
    span.log(
        output={"classification": response.content[0].text[:200]},
        metrics={"latency_ms": ..., **usage.to_braintrust()},
    )
```

### 5. Chat-Agent Span (`claude_service.py`)

Agent-level span with model config and prompt versioning.

```python
from chat.observability import traced, current_span

@traced(name="chat-agent", type="llm")
async def send_message(...):
    span = current_span()
    span.log(
        input={
            "query": last_user_message,
            "messages": formatted_messages,  # Full context for eval replay
        },
        tags=[flow, environment],
        metadata={
            # Model config
            "model": "claude-sonnet-4-5-20250929",
            "temperature": 0.7,
            "max_tokens": 8000,
            # Prompt versioning (for reproducibility)
            "core_prompt_id": "core-8fbc",
            "core_prompt_version": "stable",
            "flow_prompt_id": "bt_prompt_halachic",
            "flow_prompt_version": "stable",
        },
    )
    # ... agent loop ...
    span.log(
        output={
            "response": output,
            "refs": ["Genesis 1:1", "Rashi on Genesis 1:1"],
            "tool_calls": [...],
            "was_refused": False,
        },
        metadata={"tools_used": ["get_text", "text_search"]},
        metrics={
            "latency_ms": ...,
            "llm_calls": 3,  # Agent iterations only
            "tool_calls": 2,
            "prompt_tokens": ...,
            "completion_tokens": ...,
            "prompt_cache_creation_tokens": ...,
            "prompt_cached_tokens": ...,
            "tokens": ...,
        },
    )
```

### 6. LLM Call Spans (`claude_service.py`)

Individual Claude API calls within the agent loop. Nested under chat-agent span.

```python
from chat.observability import start_span

with start_span(name=f"llm-call-{iteration}", type="llm") as llm_span:
    llm_span.log(
        input={
            "messages": conversation[-3:],  # Last 3 messages for context
            "message_count": len(conversation),
        },
        metadata={
            "model": "claude-sonnet-4-5-20250929",
            "iteration": 1,
            "tools_count": 7,
        },
    )
    response = client.messages.create(...)
    usage = TokenUsage.from_anthropic(response.usage)
    llm_span.log(
        output={
            "text": "Based on the text...",
            "tool_calls": [{"name": "get_text", "input": {...}}],
            "stop_reason": "tool_use",
        },
        metrics={"latency_ms": ..., **usage.to_braintrust()},
    )
```

### 7. Tool Spans (`claude_service.py`)

Individual tool executions. Nested under chat-agent span.

```python
from chat.observability import start_span

with start_span(name=f"tool:{tool_name}", type="tool") as tool_span:
    tool_span.log(
        input=json.dumps(tool_input),
        metadata={"tool_name": "get_text", "tool_use_id": "..."},
    )
    result = await tool_executor.execute(tool_name, tool_input)
    tool_span.log(
        output=output_preview,
        metadata={"is_error": False},
        metrics={"latency_ms": ...},
    )
```

### 8. Summary LLM Span (`summary_service.py`)

Individual LLM call for conversation summarization.

```python
from chat.observability import start_span

with start_span(name="summary-llm", type="llm") as span:
    span.log(
        input={"user_message": new_user_message[:200], "flow": flow},
        metadata={"model": "claude-3-haiku-20240307"},
    )
    response = client.messages.create(...)
    usage = TokenUsage.from_anthropic(response.usage)
    span.log(
        output={"summary": response.content[0].text[:200]},
        metrics={"latency_ms": ..., **usage.to_braintrust()},
    )
```

---

## Data Flow

| Level | What's Logged | Purpose |
|-------|---------------|---------|
| **request** | Session/page context, **aggregated tokens** | Filter by user, session, page type; see total cost |
| **router** | Flow decision, reason codes, prompt IDs | Debug routing, track classifier performance |
| **guardrails-llm** | Safety decision, tokens | Debug guardrails, track safety cost |
| **flow-classifier-llm** | Classification, tokens | Debug routing decisions, track cost |
| **chat-agent** | Full messages, model config, aggregate metrics | Eval datasets, reproducibility |
| **llm-call-N** | Per-call tokens, messages, tool decisions | Debug agent loop, optimize prompts |
| **tool:X** | Input/output, latency | Debug tool performance |
| **summary-llm** | Summary output, tokens | Debug summarization, track cost |

---

## Token Usage Aggregation

The request span aggregates tokens from all LLM calls:

```python
# In views.py
total_usage = TokenUsage.zero()

# Add agent tokens
total_usage = total_usage + TokenUsage(
    input_tokens=agent_response.input_tokens,
    output_tokens=agent_response.output_tokens,
    ...
)

# Add router tokens (guardrails + classifier)
if route_result.token_usage:
    total_usage = total_usage + route_result.token_usage

# Add summary tokens
if summary_result.token_usage:
    total_usage = total_usage + summary_result.token_usage

# Log to request span
request_span.log(metrics={**total_usage.to_braintrust()})
```

---

## LLM Call Counting

The `llm_calls` metric in the request span counts all LLM calls:

- **Agent iterations**: Varies (1-10+ depending on tool use)
- **Router**: Up to 2 calls (guardrails + classifier when AI enabled)
- **Summary**: 1 call (when LLM summarization enabled)

```python
total_llm_calls = (
    agent_response.llm_calls +  # Agent iterations
    (2 if route_result.token_usage else 0) +  # Router LLM calls
    (1 if summary_result.token_usage else 0)  # Summary LLM call
)
```

---

## Verification Checklist

After deployment, verify in Braintrust UI:

- [x] Request span visible with session context
- [x] Request span shows **total** tokens from all LLM calls
- [x] Router span shows flow, confidence, reason codes
- [x] Guardrails-llm span shows safety decision with tokens
- [x] Flow-classifier-llm span shows classification with tokens
- [x] LLM call spans show per-call token usage
- [x] Tool spans nested under chat-agent
- [x] Summary-llm span shows summary with tokens
- [x] `output.refs` shows Sefaria references
- [x] Refusals appear with `was_refused: true`
- [x] Tags allow filtering by flow and environment
- [x] Cost estimates accurate (all LLM calls captured)

---

## File Locations

| File | Purpose |
|------|---------|
| `chat/observability/` | Tracing abstraction (`start_span`, `create_span`, `traced`) |
| `chat/observability/backends.py` | `BraintrustBackend` implementation |
| `chat/metrics.py` | `TokenUsage` dataclass with `to_braintrust()` conversion |
| `chat/orchestrator.py` | Request span, token aggregation, LLM call counting |
| `chat/router/router_service.py` | Router span, token accumulation from guardrails + classifier |
| `chat/router/ai_guardrails.py` | Guardrails-llm span |
| `chat/router/ai_router.py` | Flow-classifier-llm span |
| `chat/agent/claude_service.py` | Chat-agent span, llm-call-N spans, tool spans |
| `chat/summarization/summary_service.py` | Summary-llm span |

---

## References

- [Braintrust Write Logs](https://www.braintrust.dev/docs/guides/logs/write)
- [Braintrust Customize Traces](https://www.braintrust.dev/docs/guides/traces/customize)
- [Braintrust Cookbook](https://github.com/braintrustdata/braintrust-cookbook)
