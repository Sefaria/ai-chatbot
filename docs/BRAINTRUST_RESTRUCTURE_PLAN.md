# Braintrust Logging Restructure Plan

> **Status:** ✅ IMPLEMENTED
> **Note:** This is a temporary planning document. Remove after PR merge.

---

## Summary

Restructured trace logging to follow Braintrust best practices for eval-ready data.

**Core change:** Moved from logging strings to logging structured objects.

| Before | After |
|--------|-------|
| `input = "What does Genesis 1:1 say?"` | `input = {query: "...", messages: [...]}` |
| `output = "Genesis 1:1 states..."` | `output = {response: "...", refs: [...], tool_calls: [...]}` |

**Benefits:**
- Create eval datasets from production logs
- Score citation accuracy via `refs` field
- Filter by flow and environment via tags
- Track full conversation context
- **Refusals now logged** (previously invisible)

---

## Implementation Progress

| Task | Status | Notes |
|------|--------|-------|
| Task 1: Add channel to serializer | ⏭️ SKIPPED | Slack bot uses separate MCP architecture |
| Task 2: Extract page context in views.py | ✅ DONE | Added `extract_page_type()` |
| Task 3: Update send_message signature | ✅ DONE | Added site, page_type, page_url, client_version |
| Task 4: Restructure initial span.log | ✅ DONE | Structured input with query + messages array |
| Task 5: Restructure final span.log | ✅ DONE | Structured output with response, refs, tool_calls |
| Task 6: Store tool_output in tool_calls_list | ✅ DONE | For final logging |
| Task 7: Add refusal logging | ✅ DONE | **CRITICAL** - was completely invisible before |
| Task 8: Update frontend | ⏭️ SKIPPED | No channel field needed |

**Tests:** 17 new tests in `test_braintrust_helpers.py`

---

## What Was Implemented

### Structured Input
```python
span.log(
    input={
        "query": last_user_message,  # Current turn for quick viewing
        "messages": formatted_messages,  # Full context for eval replay
    },
    tags=[flow, environment],
    metadata={...}
)
```

### Structured Output
```python
span.log(
    output={
        "response": output,
        "refs": extract_refs(tool_calls_list),  # ["Genesis 1:1", ...]
        "tool_calls": tool_calls_summary,
        "was_refused": False,
    },
    metrics={...}
)
```

### Refusal Logging (Critical Fix)
Previously, `_create_refusal_response` returned before any `span.log()` call. Now logs:
- Full input with query and messages
- Output with `was_refused: True` and `refusal_codes`
- Same structure as normal requests for consistency

### Helper Functions
- `extract_page_type(url)` - Parse Sefaria URLs to identify page types (home, reader, eval, etc.)
- `extract_refs(tool_calls)` - Extract Sefaria refs from tool calls for citation scoring

---

## What Was Skipped

**Channel field (Tasks 1 & 8):** The Slack bot (`slack-mcp`) uses a separate architecture that calls Claude directly with MCP tools. It does not go through this API, so there's no client to send `channel: 'slack'`. Can be added later if needed.

---

## Verification Checklist

After deployment, verify in Braintrust UI:

- [ ] `input.query` shows current user message
- [ ] `input.messages` shows full conversation array
- [ ] `output.response` shows assistant text
- [ ] `output.refs` shows list of Sefaria references
- [ ] `output.tool_calls` shows tool details
- [ ] `tags` contains flow and environment
- [ ] Refusals appear with `was_refused: true`
- [ ] `metadata.page_type` identifies home/reader/eval

---

## References

- [Braintrust Write Logs](https://www.braintrust.dev/docs/guides/logs/write)
- [Braintrust Customize Traces](https://www.braintrust.dev/docs/guides/traces/customize)
- [Braintrust Cookbook](https://github.com/braintrustdata/braintrust-cookbook)
