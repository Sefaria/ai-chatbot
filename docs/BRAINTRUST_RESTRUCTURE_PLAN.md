# Braintrust Logging Restructure Plan

## Overview

This plan restructures the Braintrust trace logging in `claude_service.py` to follow best practices for eval-ready data and improved observability.

## Current State

**File:** `server/chat/agent/claude_service.py`
**Logging Method:** Native `@traced` decorator with `current_span().log()`

### Current Structure (Lines 237-252, 411-427)

```python
# Initial log (line 237)
span.log(
    input=truncate(last_user_message, 2000),  # String only
    metadata={
        'model': self.model,
        'flow': route_result.flow.value,
        'session_id': session_id or '',
        'user_id': user_id or '',
        'turn_id': turn_id or '',
        'decision_id': route_result.decision_id,
        'core_prompt_id': prompt_bundle.core_prompt_id,
        'core_prompt_version': prompt_bundle.core_prompt_version,
        'flow_prompt_id': prompt_bundle.flow_prompt_id,
        'flow_prompt_version': prompt_bundle.flow_prompt_version,
        'tools_available': route_result.tools,
    }
)

# Final log (line 411)
span.log(
    output=output,  # String only
    metadata={
        'outputLength': len(output),
        'toolNames': [tc['tool_name'] for tc in tool_calls_list],
    },
    metrics={
        'llm_calls': llm_calls,
        'tool_calls': len(tool_calls_list),
        'prompt_tokens': input_tokens,
        'completion_tokens': output_tokens,
        'cache_creation_input_tokens': cache_creation_tokens,
        'cache_read_input_tokens': cache_read_tokens,
        'total_tokens': input_tokens + output_tokens + cache_creation_tokens,
        'latency_ms': latency_ms,
    }
)
```

### Issues with Current Structure

1. **input** is a truncated string - loses conversation context and doesn't enable "Try prompt" button
2. **output** is a string - loses tool call details
3. **flow** is in metadata - should be in tags for filtering
4. **tags** is empty - missing useful categorization
5. **No channel/site tracking** - can't distinguish web vs Slack vs embedded contexts
6. **latest message not easily accessible** - buried in messages array

---

## Target Structure

### Input (what the LLM received)

```python
"input": {
    "query": "What does Genesis 1:1 say?",  # Latest user message (easy access)
    "messages": [                            # Full conversation (OpenAI format)
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
        {"role": "user", "content": "What does Genesis 1:1 say?"}
    ],
    "system_prompt_id": "core-8fbc",        # For reproducibility
}
```

**Why:**
- `query` provides easy access to current question (for eval scoring, search, etc.)
- `messages` array enables "Try prompt" button in Braintrust UI
- `messages` makes logs directly reusable as eval dataset rows

### Output (what the LLM produced)

```python
"output": {
    "response": "Genesis 1:1 states 'In the beginning...'",  # Final text response
    "tool_calls": [
        {
            "name": "get_text",
            "input": {"ref": "Genesis.1.1"},
            "output_preview": "In the beginning God created..."  # Truncated
        }
    ],
    "was_refused": False,
}
```

**Why:**
- `response` field is easy to score with LLM judges
- Tool calls visible for debugging retrieval quality
- `was_refused` enables filtering out refusals from eval sets

### Metadata (searchable context)

```python
"metadata": {
    # Session tracking
    "session_id": "sess_abc123",
    "turn_id": "turn_xyz789",
    "user_id": "user_456",

    # Model configuration
    "model": "claude-sonnet-4-5-20250929",
    "temperature": 0.7,
    "max_tokens": 8000,

    # Routing/prompts (for reproducibility)
    "decision_id": "dec_123",
    "core_prompt_id": "core-8fbc",
    "core_prompt_version": "stable",
    "flow_prompt_id": "bt_prompt_search",
    "flow_prompt_version": "local",

    # Tools
    "tools_available": ["get_text", "search_texts", ...],
    "tools_used": ["get_text"],

    # Context (for future use)
    "channel": "web",           # web | slack | api
    "site": "sefaria.org",      # Extracted from pageUrl
    "subdomain": "www",         # For analytics
    "page_url": "https://..."   # Full URL if needed
}
```

**Why:**
- All fields searchable in Braintrust UI (`metadata.user_id = 'xxx'`)
- Enables filtering by channel, site, model version
- Prompt IDs enable reproducing exact conditions

### Tags (categorical filters)

```python
"tags": [
    "search",       # flow type (lowercase)
    "web",          # channel
    "prod",         # environment
]
```

**Why:**
- Fast filtering in UI: "show me all Slack SEARCH queries in prod"
- Enables per-channel/per-site eval datasets
- Better analytics segmentation

### Metrics (numeric performance data)

```python
"metrics": {
    "latency_ms": 2340,
    "llm_calls": 2,
    "tool_calls": 1,
    "prompt_tokens": 1500,
    "completion_tokens": 800,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 500,
    "total_tokens": 2300,
    "estimated_cost_usd": 0.012,  # Optional
}
```

---

## Implementation Tasks

### Task 1: Add channel/site to API request

**File:** `server/chat/serializers.py`

Add `channel` field to `MessageContextSerializer`:

```python
class MessageContextSerializer(serializers.Serializer):
    """Context information sent with each message."""
    pageUrl = serializers.URLField(required=False, allow_blank=True)
    locale = serializers.CharField(max_length=10, required=False, allow_blank=True)
    clientVersion = serializers.CharField(max_length=20, required=False, allow_blank=True)
    channel = serializers.ChoiceField(
        choices=['web', 'slack', 'api'],
        default='web',
        required=False
    )
```

### Task 2: Pass context through to agent

**File:** `server/chat/views.py`

Extract and pass context data to `send_message()`:

```python
# In _process_chat_stream or similar
context = validated_data.get('context', {})
page_url = context.get('pageUrl', '')
channel = context.get('channel', 'web')

# Extract site/subdomain from pageUrl
from urllib.parse import urlparse
parsed = urlparse(page_url) if page_url else None
site = parsed.netloc if parsed else ''
subdomain = site.split('.')[0] if site and '.' in site else ''

# Pass to agent
response = await agent.send_message(
    messages=messages,
    route_result=route_result,
    session_id=session_id,
    user_id=user_id,
    turn_id=turn_id,
    channel=channel,      # New
    site=site,            # New
    page_url=page_url,    # New
)
```

### Task 3: Update send_message signature

**File:** `server/chat/agent/claude_service.py`

Add parameters to `send_message()`:

```python
@traced(name="chat-agent", type="llm")
async def send_message(
    self,
    messages: List[ConversationMessage],
    route_result: RouteResult,
    on_progress: Optional[Callable[[AgentProgressUpdate], None]] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    channel: str = 'web',           # New
    site: str = '',                 # New
    page_url: str = '',             # New
) -> AgentResponse:
```

### Task 4: Restructure initial span.log call

**File:** `server/chat/agent/claude_service.py` (around line 237)

Replace current `span.log()` with:

```python
# Build OpenAI-format messages for input
formatted_messages = [
    {"role": m.role, "content": m.content}
    for m in messages
]

# Get environment
environment = os.environ.get('ENVIRONMENT', 'dev')

# Log structured input
span.log(
    input={
        "query": last_user_message,  # Easy access to current question
        "messages": formatted_messages,
        "system_prompt_id": prompt_bundle.core_prompt_id,
    },
    tags=[
        route_result.flow.value.lower(),  # search, halachic, general, refuse
        channel,                           # web, slack, api
        environment,                       # dev, staging, prod
    ],
    metadata={
        # Session tracking
        'session_id': session_id or '',
        'turn_id': turn_id or '',
        'user_id': user_id or '',

        # Model configuration
        'model': self.model,
        'temperature': self.temperature,
        'max_tokens': self.max_tokens,

        # Routing/prompts
        'decision_id': route_result.decision_id,
        'core_prompt_id': prompt_bundle.core_prompt_id,
        'core_prompt_version': prompt_bundle.core_prompt_version,
        'flow_prompt_id': prompt_bundle.flow_prompt_id,
        'flow_prompt_version': prompt_bundle.flow_prompt_version,

        # Tools
        'tools_available': route_result.tools,

        # Context
        'channel': channel,
        'site': site,
        'page_url': page_url,
    }
)
```

### Task 5: Restructure final span.log call

**File:** `server/chat/agent/claude_service.py` (around line 411)

Replace current final `span.log()` with:

```python
# Build tool calls summary
tool_calls_summary = [
    {
        "name": tc['tool_name'],
        "input": tc.get('tool_input', {}),
        "output_preview": truncate(str(tc.get('tool_output', '')), 500),
    }
    for tc in tool_calls_list
]

# Log structured output
span.log(
    output={
        "response": output,
        "tool_calls": tool_calls_summary,
        "was_refused": False,
    },
    metadata={
        'tools_used': [tc['tool_name'] for tc in tool_calls_list],
    },
    metrics={
        'latency_ms': latency_ms,
        'llm_calls': llm_calls,
        'tool_calls': len(tool_calls_list),
        'prompt_tokens': input_tokens,
        'completion_tokens': output_tokens,
        'cache_creation_input_tokens': cache_creation_tokens,
        'cache_read_input_tokens': cache_read_tokens,
        'total_tokens': input_tokens + output_tokens + cache_creation_tokens,
    }
)
```

### Task 6: Handle refusal responses

**File:** `server/chat/agent/claude_service.py` (in `_create_refusal_response` method)

Add span logging for refusals:

```python
def _create_refusal_response(self, route_result, start_time, **context):
    span = current_span()
    latency_ms = int((time.time() - start_time) * 1000)

    refusal_message = route_result.safety.refusal_message or "..."

    span.log(
        output={
            "response": refusal_message,
            "tool_calls": [],
            "was_refused": True,
            "refusal_codes": [code.value for code in route_result.safety.reason_codes],
        },
        metrics={
            'latency_ms': latency_ms,
            'llm_calls': 0,
            'tool_calls': 0,
        }
    )

    # ... rest of method
```

### Task 7: Update tests

Update any tests that verify logging structure to match the new format.

---

## Verification Checklist

After implementation, verify in Braintrust UI:

- [ ] `input.query` shows the current user message
- [ ] `input.messages` shows full conversation array
- [ ] "Try prompt" button works in the UI
- [ ] `output.response` shows the assistant response
- [ ] `output.tool_calls` shows tool usage
- [ ] `tags` contains flow, channel, environment
- [ ] `metadata.session_id` is searchable
- [ ] Filtering by `tags: web` or `tags: slack` works
- [ ] Refusals have `output.was_refused: true`

---

## Migration Notes

- No database migrations needed (this only affects Braintrust trace structure)
- Old logs in Braintrust will have the old structure
- Consider creating a new Braintrust project or using tags to distinguish old vs new format
- Frontend changes needed if channel needs to be passed (update API calls)

---

## References

- [Braintrust Write Logs](https://www.braintrust.dev/docs/guides/logs/write)
- [Braintrust Customize Traces](https://www.braintrust.dev/docs/guides/traces/customize)
- [Braintrust Python SDK](https://www.braintrust.dev/docs/reference/sdks/python)
