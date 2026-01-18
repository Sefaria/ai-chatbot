# Braintrust Logging Restructure Plan

> **Goal:** Restructure trace logging to follow Braintrust best practices for eval-ready data.

## References

- [Write Logs](https://www.braintrust.dev/docs/guides/logs/write) - Input/output structure
- [Customize Traces](https://www.braintrust.dev/docs/guides/traces/customize) - Span attributes
- [Python SDK](https://www.braintrust.dev/docs/reference/sdks/python) - API reference
- [Braintrust Cookbook](https://github.com/braintrustdata/braintrust-cookbook) - Examples

---

## Current State

**File:** `server/chat/agent/claude_service.py`
**Method:** Native `@traced` decorator with `current_span().log()`

### Current Structure (lines 237-252, 411-427)

```python
# Input: truncated string only
span.log(input=truncate(last_user_message, 2000), metadata={...})

# Output: string only
span.log(output=output, metadata={...}, metrics={...})
```

### Issues

| Problem | Impact |
|---------|--------|
| `input` is string | No "Try prompt" button, can't reuse for evals |
| `output` is string | Loses tool call details |
| `tags` is empty | No filtering by flow/channel/environment |
| No `query` field | Latest message buried in conversation |
| No version tracking | Can't reproduce exact conditions |

---

## Target Structure

### Input

```python
"input": {
    "query": "What does Genesis 1:1 say?",     # Easy access
    "messages": [{"role": "user", "content": "..."}],  # Enables "Try prompt"
    "system_prompt_id": "core-8fbc",
}
```

### Output

```python
"output": {
    "response": "Genesis 1:1 states...",
    "tool_calls": [{"name": "get_text", "input": {...}, "output_preview": "..."}],
    "was_refused": False,
}
```

### Metadata

```python
"metadata": {
    # Session
    "session_id": "sess_abc", "turn_id": "turn_xyz", "user_id": "user_123",

    # Model
    "model": "claude-sonnet-4-5-20250929", "temperature": 0.7, "max_tokens": 8000,

    # Prompts (already tracked - no changes needed)
    "core_prompt_id": "core-8fbc", "core_prompt_version": "stable",
    "flow_prompt_id": "bt_prompt_search", "flow_prompt_version": "local",
    "decision_id": "dec_123",

    # Tools
    "tools_available": [...], "tools_used": [...],

    # Context (new)
    "channel": "web",              # web | slack | api
    "site": "sefaria.org",         # From pageUrl
    "client_version": "1.0.0",     # From context.clientVersion
    "page_url": "https://...",
}
```

### Tags

```python
"tags": ["search", "web", "prod"]  # flow, channel, environment
```

---

## Implementation Tasks

### Task 1: Add channel to API serializer

**File:** `server/chat/serializers.py` (line 9)

```python
class MessageContextSerializer(serializers.Serializer):
    pageUrl = serializers.URLField(required=False, allow_blank=True)
    locale = serializers.CharField(max_length=10, required=False, allow_blank=True)
    clientVersion = serializers.CharField(max_length=20, required=False, allow_blank=True)
    channel = serializers.ChoiceField(          # ADD
        choices=['web', 'slack', 'api'],
        default='web',
        required=False
    )
```

### Task 2: Extract and pass context in views.py

**File:** `server/chat/views.py`

Find where `send_message()` is called and add context extraction:

```python
from urllib.parse import urlparse

# Extract context
context = validated_data.get('context', {})
page_url = context.get('pageUrl', '')
channel = context.get('channel', 'web')
client_version = context.get('clientVersion', '')

# Extract site from URL
parsed = urlparse(page_url) if page_url else None
site = parsed.netloc if parsed else ''

# Pass to agent (add new params)
response = await agent.send_message(
    ...,
    channel=channel,
    site=site,
    page_url=page_url,
    client_version=client_version,
)
```

### Task 3: Update send_message signature

**File:** `server/chat/agent/claude_service.py` (line 141)

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
    channel: str = 'web',              # NEW
    site: str = '',                    # NEW
    page_url: str = '',                # NEW
    client_version: str = '',          # NEW
) -> AgentResponse:
```

### Task 4: Restructure initial span.log

**File:** `server/chat/agent/claude_service.py` (replace lines 237-252)

```python
# Build OpenAI-format messages
formatted_messages = [{"role": m.role, "content": m.content} for m in messages]
environment = os.environ.get('ENVIRONMENT', 'dev')

span.log(
    input={
        "query": last_user_message,
        "messages": formatted_messages,
        "system_prompt_id": prompt_bundle.core_prompt_id,
    },
    tags=[
        route_result.flow.value.lower(),
        channel,
        environment,
    ],
    metadata={
        'session_id': session_id or '',
        'turn_id': turn_id or '',
        'user_id': user_id or '',
        'model': self.model,
        'temperature': self.temperature,
        'max_tokens': self.max_tokens,
        'decision_id': route_result.decision_id,
        'core_prompt_id': prompt_bundle.core_prompt_id,
        'core_prompt_version': prompt_bundle.core_prompt_version,
        'flow_prompt_id': prompt_bundle.flow_prompt_id,
        'flow_prompt_version': prompt_bundle.flow_prompt_version,
        'tools_available': route_result.tools,
        'channel': channel,
        'site': site,
        'page_url': page_url,
        'client_version': client_version,
    }
)
```

### Task 5: Restructure final span.log

**File:** `server/chat/agent/claude_service.py` (replace lines 411-427)

```python
tool_calls_summary = [
    {"name": tc['tool_name'], "input": tc.get('tool_input', {}),
     "output_preview": truncate(str(tc.get('tool_output', '')), 500)}
    for tc in tool_calls_list
]

span.log(
    output={
        "response": output,
        "tool_calls": tool_calls_summary,
        "was_refused": False,
    },
    metadata={'tools_used': [tc['tool_name'] for tc in tool_calls_list]},
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

### Task 6: Handle refusals

**File:** `server/chat/agent/claude_service.py` - `_create_refusal_response` method (around line 447)

Add span logging for refusal responses:

```python
def _create_refusal_response(self, route_result, start_time, ...):
    span = current_span()
    latency_ms = int((time.time() - start_time) * 1000)

    span.log(
        output={
            "response": refusal_message,
            "tool_calls": [],
            "was_refused": True,
            "refusal_codes": [c.value for c in route_result.safety.reason_codes],
        },
        metrics={'latency_ms': latency_ms, 'llm_calls': 0, 'tool_calls': 0}
    )
    # ... rest of method
```

### Task 7: Update frontend (optional)

**File:** `src/lib/api.js`

If Slack bot needs to send `channel: 'slack'`, update the API call context. Currently `channel` defaults to `'web'`.

---

## Verification Checklist

After deployment, verify in Braintrust UI:

- [ ] `input.query` shows current user message
- [ ] `input.messages` shows conversation array
- [ ] "Try prompt" button works
- [ ] `output.response` shows assistant text
- [ ] `output.tool_calls` shows tool details
- [ ] `tags` contains flow, channel, environment
- [ ] Filtering by `tags: slack` works
- [ ] Refusals have `output.was_refused: true`
- [ ] `metadata.client_version` shows app version

---

## Notes

- **No database migrations** - only Braintrust trace structure changes
- **Prompt versions already tracked** - `core_prompt_version`, `flow_prompt_version` exist
- **Bot version** - `client_version` comes from `context.clientVersion` (currently "1.0.0")
- **Old logs** - Will have old structure; use tags or date to distinguish
