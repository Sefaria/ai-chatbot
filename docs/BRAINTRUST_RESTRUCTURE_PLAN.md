# Braintrust Logging Restructure Plan

> **Goal:** Restructure trace logging to follow Braintrust best practices for eval-ready data.

---

## TL;DR - Why This Matters

**The core change:** We're moving from logging strings to logging structured objects.

| Before | After |
|--------|-------|
| `input = "What does Genesis 1:1 say?"` | `input = {query: "...", messages: [...]}` |
| `output = "Genesis 1:1 states..."` | `output = {response: "...", refs: [...], tool_calls: [...]}` |

This enables:
- Creating eval datasets from production logs
- Scoring citation accuracy
- Filtering by flow, channel, and environment
- Tracking full conversation context

| Issue | Impact | Priority |
|-------|--------|----------|
| **Refusals aren't logged** | Zero visibility into refused requests - can't analyze safety patterns | **Critical** |
| **Input is a truncated string** | Can't create eval datasets, can't replay conversations | High |
| **No tags** | Can't filter by flow/channel/environment in UI | High |
| **No page context** | Can't distinguish home page from reader etc | Medium |
| **No refs in output** | Can't score citation accuracy without parsing markdown | Medium |

---

## References

**Official Braintrust docs:**
- [Write Logs](https://www.braintrust.dev/docs/guides/logs/write) - Input/output structure
- [Customize Traces](https://www.braintrust.dev/docs/guides/traces/customize) - Span attributes
- [Python SDK](https://www.braintrust.dev/docs/reference/sdks/python) - API reference
- [Braintrust Cookbook](https://github.com/braintrustdata/braintrust-cookbook) - Official examples (see EvaluatingChatAssistant for chat format)

**Provided by Braintrust team:**
- [braintrust-mastra-app](https://github.com/philhetzel/braintrust-mastra-app) - Reference implementation sent to us directly

---

## Current State

**File:** `server/chat/agent/claude_service.py`
**Method:** Native `@traced` decorator with `current_span().log()`

### Current Structure (lines 237-252, 411-427)

```python
# Input: truncated string only - loses conversation context
span.log(input=truncate(last_user_message, 2000), metadata={...})

# Output: string only - loses tool call details and refs
span.log(output=output, metadata={...}, metrics={...})
```

### Current Gaps

| Problem | Why It Matters |
|---------|----------------|
| `input` is string | Can't create eval datasets - need structured messages array |
| `output` is string | Can't score tool usage or citation accuracy |
| `tags` is empty | Can't filter by flow/channel/env in Braintrust UI |
| No `refs` field | Would need to parse markdown to extract citations |
| No page context | Can't segment by eval vs prod, or reader vs home |
| **Refusals not logged** | `_create_refusal_response` returns before any `span.log()` call |

---

## Target Structure

### Input

```python
# Why structured: Enables eval dataset creation from production logs.
# The messages array can be replayed through the agent for regression testing.
#
# Format: We use the OpenAI chat format (role/content) because:
# - It's what the Braintrust Cookbook uses for chat evals
# - Braintrust is flexible, but this format is widely understood
# - It matches what we send to Claude anyway
#
# Note: If we find we can easily reconstruct history at query time, we may
# change to logging only the current turn.
"input": {
    "query": "What does Genesis 1:1 say?",     # Current turn - easy to see in UI
    "messages": [                              # Full conversation sent to Claude
        {"role": "system", "content": "..."},  # Actual system prompt content
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "..."},
    ],
}
```

### Output

```python
# Why structured: Enables scoring of tool usage and citation accuracy.
"output": {
    "response": "Genesis 1:1 states...",       # Final text response
    "refs": ["Genesis 1:1", "Rashi on Genesis 1:1"],  # Extracted from tool_input['reference']
    "tool_calls": [
        {
            "name": "get_text",
            "input": {"reference": "Genesis 1:1"},
            "output_preview": "...",           # Truncated to 500 chars normally
            "output_full": "...",              # Only included on errors for debugging
            "is_error": False,
        }
    ],
    "was_refused": False,
}
```

### Metadata

```python
"metadata": {
    # Session - for grouping related requests
    "session_id": "sess_abc",
    "turn_id": "turn_xyz",
    "user_id": "user_123",

    # Model config - for reproducing conditions
    "model": "claude-sonnet-4-5-20250929",
    "temperature": 0.7,
    "max_tokens": 8000,

    # Prompt versioning - Braintrust maintains version history by prompt ID
    "decision_id": "dec_123",                  # Router decision
    "core_prompt_id": "core-8fbc",
    "core_prompt_version": "stable",           # or "local" for dev
    "flow_prompt_id": "bt_prompt_search",
    "flow_prompt_version": "stable",

    # Tools
    "tools_available": ["get_text", "search_texts", ...],
    "tools_used": ["get_text"],

    # Page context - for segmenting traffic
    "channel": "web",                          # web | slack | api
    "site": "sefaria.org",                     # Domain: sefaria.org | eval.sefaria.org
    "page_type": "reader",                     # home | reader | search | topics | eval | other
    "page_url": "https://www.sefaria.org/Genesis.1",
    "client_version": "1.0.0",
}
```

### Tags

```python
# Why tags: Enable quick filtering in Braintrust UI.
# Tags are aggregated across all spans in a trace.
"tags": [
    "search",     # Flow type: search | halachic | general | refuse
    "web",        # Channel: web | slack | api
    "prod",       # Environment: dev | staging | prod
]
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
    # Why: Slack bot and API clients need to identify themselves
    channel = serializers.ChoiceField(
        choices=['web', 'slack', 'api'],
        default='web',
        required=False
    )
```

### Task 2: Extract and pass context in views.py

**File:** `server/chat/views.py`

```python
from urllib.parse import urlparse

def extract_page_type(url: str) -> str:
    """
    Extract page type from Sefaria URL.
    Why: Different pages have different usage patterns we want to analyze separately.

    Returns:
        - Subdomain if present (e.g., 'eval' from eval.sefaria.org)
        - 'home' for /texts (the actual home page)
        - 'reader' for text pages (most common)
        - 'other' for anything else
    """
    if not url:
        return 'unknown'

    parsed = urlparse(url)

    # Check for subdomain first (e.g., eval.sefaria.org -> 'eval')
    # Subdomains indicate different environments/features
    host_parts = parsed.netloc.split('.')
    if len(host_parts) > 2 and host_parts[0] not in ['www']:
        return host_parts[0]  # 'eval', 'staging', etc.

    path = parsed.path.lower()

    # Known page types
    if path in ['/texts', '/texts/']:
        return 'home'  # /texts is the Sefaria home page
    # Reader pages are text references (most common) - start with /TextName
    if path and path != '/' and not path.startswith('/static'):
        return 'reader'

    return 'other'

# In the view function:
context = validated_data.get('context', {})
page_url = context.get('pageUrl', '')
channel = context.get('channel', 'web')
client_version = context.get('clientVersion', '')

# Extract site (domain) and page type
parsed = urlparse(page_url) if page_url else None
site = parsed.netloc if parsed else ''  # e.g., "eval.sefaria.org" or "www.sefaria.org"
page_type = extract_page_type(page_url)

# Pass to agent
response = await agent.send_message(
    ...,
    channel=channel,
    site=site,
    page_type=page_type,
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
    # New context params for Braintrust logging
    channel: str = 'web',
    site: str = '',
    page_type: str = 'unknown',
    page_url: str = '',
    client_version: str = '',
) -> AgentResponse:
```

### Task 4: Restructure initial span.log

**File:** `server/chat/agent/claude_service.py` (replace lines 237-252)

```python
# Build messages array in OpenAI format - includes full conversation history
# sent to Claude for this turn. This enables creating eval datasets from logs.
formatted_messages = [
    {"role": "system", "content": prompt_bundle.system_prompt},
    *[{"role": m.role, "content": m.content} for m in messages]
]
environment = os.environ.get('ENVIRONMENT', 'dev')

span.log(
    input={
        "query": last_user_message,  # Current turn for quick viewing
        "messages": formatted_messages,  # Full context for eval replay
    },
    tags=[
        route_result.flow.value.lower(),  # search | halachic | general
        channel,                           # web | slack | api
        environment,                       # dev | staging | prod
    ],
    metadata={
        # Session context
        'session_id': session_id or '',
        'turn_id': turn_id or '',
        'user_id': user_id or '',

        # Model config
        'model': self.model,
        'temperature': self.temperature,
        'max_tokens': self.max_tokens,

        # Prompt versioning (Braintrust maintains version history)
        'decision_id': route_result.decision_id,
        'core_prompt_id': prompt_bundle.core_prompt_id,
        'core_prompt_version': prompt_bundle.core_prompt_version,
        'flow_prompt_id': prompt_bundle.flow_prompt_id,
        'flow_prompt_version': prompt_bundle.flow_prompt_version,

        # Tools
        'tools_available': route_result.tools,

        # Page context - for traffic segmentation
        'channel': channel,
        'site': site,              # Domain for eval vs prod filtering
        'page_type': page_type,    # home | reader | search | etc.
        'page_url': page_url,
        'client_version': client_version,
    }
)
```

### Task 5: Restructure final span.log

**File:** `server/chat/agent/claude_service.py` (replace lines 411-427)

```python
def extract_refs(tool_calls: list) -> list:
    """
    Extract Sefaria refs from tool calls.
    Why: Enables scoring citation accuracy without parsing response markdown.
    Refs are already available in tool_input['reference'] for text-fetching tools.
    """
    refs = []
    for tc in tool_calls:
        ref = tc.get('tool_input', {}).get('reference')
        if ref and ref not in refs:
            refs.append(ref)
    return refs

# Build tool calls summary with conditional full output on errors
tool_calls_summary = []
for tc in tool_calls_list:
    tool_summary = {
        "name": tc['tool_name'],
        "input": tc.get('tool_input', {}),
        "output_preview": truncate(str(tc.get('tool_output', '')), 500),
        "is_error": tc.get('is_error', False),
    }
    # Include full output only on errors for debugging
    if tc.get('is_error'):
        tool_summary["output_full"] = str(tc.get('tool_output', ''))
    tool_calls_summary.append(tool_summary)

span.log(
    output={
        "response": output,
        "refs": extract_refs(tool_calls_list),  # ["Genesis 1:1", "Rashi on Genesis 1:1"]
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

### Task 6: Store tool_output in tool_calls_list

**File:** `server/chat/agent/claude_service.py` (around line 366)

Currently `tool_calls_list` doesn't include `tool_output`. Update the data structure:

```python
# Track tool call - include output for final logging
tool_call_data = {
    'tool_name': tool_name,
    'tool_input': tool_input,
    'tool_use_id': tool_use_id,
    'tool_output': output_text,  # ADD: Store for final span.log
    'is_error': result.is_error,
    'latency_ms': tool_latency,
}
tool_calls_list.append(tool_call_data)
```

### Task 7: Add refusal logging (CRITICAL)

**File:** `server/chat/agent/claude_service.py` - `_create_refusal_response` method

**Why this is critical:** Currently, refusals return before any `span.log()` call, meaning we have ZERO visibility into refused requests. We can't analyze safety patterns, identify false positives, or understand what users are trying to do that gets blocked.

**Code fix needed:** The `last_user_message` extraction (line 174) happens AFTER the refusal check (line 170). Move it before:

```python
# Around line 166, BEFORE the refusal check:
start_time = time.time()
span = current_span()

# Extract last user message FIRST - needed for both refusal and normal logging
last_user_message = next(
    (m.content for m in reversed(messages) if m.role == 'user'),
    ''
)

# Handle refusal flow
if route_result.flow == Flow.REFUSE:
    return self._create_refusal_response(
        route_result, start_time, messages, last_user_message, ...
    )
```

```python
def _create_refusal_response(
    self,
    route_result: RouteResult,
    start_time: float,
    # Add params needed for logging
    messages: List[ConversationMessage],
    last_user_message: str,
    channel: str = 'web',
    site: str = '',
    page_type: str = 'unknown',
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    turn_id: Optional[str] = None,
) -> AgentResponse:
    """Create a response for refused requests."""
    span = current_span()
    latency_ms = int((time.time() - start_time) * 1000)
    environment = os.environ.get('ENVIRONMENT', 'dev')

    refusal_message = route_result.safety.refusal_message or \
        "I'm not able to help with that request."

    # Log input (same structure as normal requests for consistency)
    span.log(
        input={
            "query": last_user_message,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        },
        tags=[
            'refuse',  # Always 'refuse' for this flow
            channel,
            environment,
        ],
        metadata={
            'session_id': session_id or '',
            'turn_id': turn_id or '',
            'user_id': user_id or '',
            'decision_id': route_result.decision_id,
            'channel': channel,
            'site': site,
            'page_type': page_type,
        }
    )

    # Log output with refusal details
    span.log(
        output={
            "response": refusal_message,
            "refs": [],
            "tool_calls": [],
            "was_refused": True,
            "refusal_codes": [c.value for c in route_result.safety.reason_codes],
        },
        metrics={
            'latency_ms': latency_ms,
            'llm_calls': 0,
            'tool_calls': 0,
            'prompt_tokens': 0,
            'completion_tokens': 0,
        }
    )

    logger.warning(f"Request refused: {route_result.safety.refusal_message}")

    return AgentResponse(
        content=refusal_message,
        tool_calls=[],
        llm_calls=0,
        input_tokens=0,
        output_tokens=0,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        latency_ms=latency_ms,
        flow=route_result.flow.value,
        decision_id=route_result.decision_id,
        was_refused=True,
    )
```

Also update the call site (around line 170) to pass the required params:

```python
if route_result.flow == Flow.REFUSE:
    return self._create_refusal_response(
        route_result,
        start_time,
        messages=messages,
        last_user_message=last_user_message,
        channel=channel,
        site=site,
        page_type=page_type,
        session_id=session_id,
        user_id=user_id,
        turn_id=turn_id,
    )
```

### Task 8: Update frontend (optional)

**File:** `src/lib/api.js`

If Slack bot or other clients need to identify themselves:

```javascript
const context = {
  pageUrl: window.location.href,
  clientVersion: '1.0.0',
  channel: 'web',  // Slack bot would send 'slack'
};
```

---

## Verification Checklist

After deployment, verify in Braintrust UI:

**Input/Output Structure:**
- [ ] `input.query` shows current user message
- [ ] `input.messages` shows full conversation array with system prompt
- [ ] `output.response` shows assistant text
- [ ] `output.refs` shows list of Sefaria references used
- [ ] `output.tool_calls` shows tool details with input/output

**Tags & Filtering:**
- [ ] `tags` contains flow, channel, environment
- [ ] Can filter by `tags: slack` to see Slack traffic
- [ ] Can filter by `tags: refuse` to see refusals
- [ ] Can filter by `tags: search` vs `tags: general`

**Page Context:**
- [ ] `metadata.site` shows domain (sefaria.org vs eval.sefaria.org)
- [ ] `metadata.page_type` correctly identifies home/reader/search
- [ ] Can segment eval traffic from production

**Refusals:**
- [ ] Refusals now appear in logs (previously invisible!)
- [ ] `output.was_refused: true` for refused requests
- [ ] `output.refusal_codes` shows why request was refused

**Error Handling:**
- [ ] Tool errors include `output_full` for debugging
- [ ] Normal tool calls only have `output_preview`

---

## Pricing Considerations

Braintrust pricing (Pro tier):
- **Spans:** Unlimited
- **Data:** 5GB included, $3/GB after
- **Retention:** 1 month included, $3/GB/month after

**Cost mitigation in this plan:**
- Tool outputs truncated to 500 chars (full only on errors)
- No large blob storage (tool results stay in nested spans)

---

## Future Considerations

1. **Eval workflow:** Plan supports both:
   - Export logs → create datasets → run evals (current approach)
   - Score production logs directly (future)

2. **Full history optimization:** If we find we can easily reconstruct conversation history at query time via session_id, we could reduce `input.messages` to just the current turn.

3. **Router logging:** Currently only the main agent is logged. Consider adding a separate span for router decisions if we need to debug routing accuracy.

---

## Notes

- **No database migrations** - only Braintrust trace structure changes
- **Prompt versions** - Braintrust maintains version history by prompt ID
- **Old logs** - Will have old structure; use tags or date to distinguish
- **Backwards compatible** - Old clients without `channel` field default to 'web'
