# Braintrust Logging Structure

> **Status:** ✅ IMPLEMENTED
> **Last Updated:** January 2025

---

## Summary

Hierarchical trace logging optimized for evaluations and debugging.

**Trace Hierarchy:**
```
request (views.py)
├── router (router_service.py)
│   └── Routing decision, flow classification, prompt IDs
│
└── chat-agent (claude_service.py)
    ├── llm-call-1 (individual Claude API call)
    ├── tool:get_text
    ├── tool:text_search
    └── llm-call-2 (with tool results)
```

**Benefits:**
- See routing decisions at a glance (not buried in metadata)
- Debug individual LLM calls within the agent loop
- Track token usage per call, not just aggregate
- Create eval datasets from production logs
- Score citation accuracy via `refs` field

---

## Span Details

### 1. Request Span (`views.py`)

Top-level span wrapping the entire request.

```python
with braintrust.start_span(name="request", type="task") as request_span:
    request_span.log(
        input={"query": user_message},
        metadata={
            "session_id": "...",
            "user_id": "...",
            "turn_id": "...",
            "site": "www.sefaria.org",
            "page_type": "reader",
            "page_url": "...",
            "client_version": "...",
        },
        tags=[environment],  # dev | staging | prod
    )
    # ... router and agent calls ...
    request_span.log(
        output={"response": "..."},
        tags=[flow],  # search | halachic | general
        metrics={
            "latency_ms": ...,
            "llm_calls": ...,
            "tool_calls": ...,
            "input_tokens": ...,
            "output_tokens": ...,
        },
    )
```

### 2. Router Span (`router_service.py`)

Captures the routing decision with full context.

```python
@traced(name="router", type="function")
def route(...):
    span.log(
        input={
            "query": user_message,
            "conversation_summary": "...",
            "previous_flow": "GENERAL",
        },
    )
    # ... guardrails and classification ...
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

### 3. Chat-Agent Span (`claude_service.py`)

Agent-level span with model config and prompt versioning.

```python
@traced(name="chat-agent", type="llm")
async def send_message(...):
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
            "llm_calls": 3,
            "tool_calls": 2,
            "prompt_tokens": ...,
            "completion_tokens": ...,
            "cache_creation_input_tokens": ...,
            "cache_read_input_tokens": ...,
            "total_tokens": ...,
        },
    )
```

### 4. LLM Call Spans (`claude_service.py`)

Individual Claude API calls within the agent loop.

```python
with braintrust.start_span(name=f"llm-call-{iteration}", type="llm") as llm_span:
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
    llm_span.log(
        output={
            "text": "Based on the text...",
            "tool_calls": [{"name": "get_text", "input": {...}}],
            "stop_reason": "tool_use",
        },
        metrics={
            "latency_ms": ...,
            "prompt_tokens": ...,
            "completion_tokens": ...,
            "cache_creation_tokens": ...,
            "cache_read_tokens": ...,
        },
    )
```

### 5. Tool Spans (`claude_service.py`)

Individual tool executions (unchanged from before).

```python
@traced(name=f"tool:{tool_name}", type="tool")
async def execute_tool_traced():
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

---

## Data Flow

| Level | What's Logged | Purpose |
|-------|---------------|---------|
| **request** | Session/page context | Filter by user, session, page type |
| **router** | Flow decision, reason codes, prompt IDs | Debug routing, track classifier performance |
| **chat-agent** | Full messages, model config, aggregate metrics | Eval datasets, reproducibility |
| **llm-call-N** | Per-call tokens, messages, tool decisions | Debug agent loop, optimize prompts |
| **tool:X** | Input/output, latency | Debug tool performance |

---

## Verification Checklist

After deployment, verify in Braintrust UI:

- [ ] Request span visible with session context
- [ ] Router span shows flow, confidence, reason codes
- [ ] LLM call spans show per-call token usage
- [ ] Tool spans nested under chat-agent
- [ ] `output.refs` shows Sefaria references
- [ ] Refusals appear with `was_refused: true`
- [ ] Tags allow filtering by flow and environment

---

## References

- [Braintrust Write Logs](https://www.braintrust.dev/docs/guides/logs/write)
- [Braintrust Customize Traces](https://www.braintrust.dev/docs/guides/traces/customize)
- [Braintrust Cookbook](https://github.com/braintrustdata/braintrust-cookbook)
