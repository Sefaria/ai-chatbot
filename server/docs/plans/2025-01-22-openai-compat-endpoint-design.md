# OpenAI-Compatible Endpoint for Braintrust Integration

**Date:** 2025-01-22
**Status:** Approved

## Overview

Add an OpenAI-compatible adapter endpoint to enable querying the deployed Sefaria agent from Braintrust's playground UI. This allows testing the full agent (routing + tools + Sefaria API) without modifying the existing `/api/chat` endpoint used by the frontend widget.

## Background

Braintrust's Custom Providers feature requires endpoints to follow standard API formats (OpenAI, Anthropic, Google). Our current `/api/chat` endpoint uses a custom format with rich context fields (userId, sessionId, pageUrl, locale, routing metadata) that doesn't map cleanly to OpenAI format.

Rather than lose this context data or maintain two incompatible formats for the main API, we add a thin adapter endpoint specifically for Braintrust integration.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Endpoint path | `POST /api/v1/chat/completions` | Standard OpenAI path, Braintrust recognizes automatically |
| Authentication | None | Matches current `/api/chat` pattern |
| Response format | OpenAI + `routing` metadata | Enables debugging visibility in Braintrust |
| Model name | `sefaria-agent` | Descriptive identifier |
| Streaming | No (initially) | Simplicity; sufficient for playground testing |

## Request/Response Format

### Request

```json
{
  "model": "sefaria-agent",
  "messages": [
    {"role": "user", "content": "What is Shabbat?"}
  ]
}
```

### Response

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1705912345,
  "model": "sefaria-agent",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "Shabbat is the Jewish day of rest..."
    },
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 150,
    "completion_tokens": 280,
    "total_tokens": 430
  },
  "routing": {
    "flow": "GENERAL",
    "decision_id": "route-xyz789",
    "confidence": 0.92,
    "was_refused": false
  }
}
```

### Error Response

```json
{
  "error": {
    "message": "Invalid request: messages array is required",
    "type": "invalid_request_error",
    "code": "missing_required_field"
  }
}
```

## Logging & Traceability

- Log prefix: `[openai-compat]` to distinguish from regular `/api/chat`
- Internal context sets `source = "braintrust"`
- Generated IDs prefixed with `bt-` for easy identification
- Flows through to Braintrust spans and database records

Example logs:
```
📨 [openai-compat] user=bt-abc123 session=bt-xyz789 text=What is Shabbat?...
🔀 [openai-compat] Route: flow=GENERAL confidence=0.92
📤 [openai-compat] response=msg-123... latency=2450ms tokens=150+280
```

## Implementation

### Files to Modify

| File | Change |
|------|--------|
| `chat/tests/test_openai_compat.py` | New - all tests (TDD) |
| `chat/views.py` | Add `openai_chat_completions` view |
| `chat/urls.py` | Add route for `/v1/chat/completions` |
| `chat/serializers.py` | Add `OpenAIChatRequestSerializer` |

### Test Coverage

```python
class TestOpenAICompatEndpoint:
    # Request validation
    test_rejects_missing_messages()
    test_rejects_empty_messages()
    test_rejects_invalid_message_format()

    # Request transformation
    test_extracts_last_user_message()
    test_handles_multi_turn_conversation()
    test_generates_bt_prefixed_session_id()

    # Response transformation
    test_returns_openai_format_structure()
    test_includes_usage_tokens()
    test_includes_routing_metadata()
    test_maps_content_to_choices()

    # Error handling
    test_agent_error_returns_openai_error_format()
    test_validation_error_returns_400()

    # Logging/traceability
    test_sets_braintrust_source_in_context()
```

## Usage in Braintrust

After implementation:

1. Go to Braintrust Settings → AI providers (requires org admin)
2. Create Custom Provider:
   - Name: `Sefaria Agent`
   - Model: `sefaria-agent`
   - Endpoint: `https://chat-dev.sefaria.org/api/v1/chat/completions`
   - Format: `openai`
3. Use in Playground as a model option

## Documentation Link

Custom Providers documentation: https://www.braintrust.dev/docs/integrations/ai-providers/custom
