# Tracing Improvements Plan

Goal: Improve Braintrust span logging in the v2 API so traces are useful for debugging and observability.

## Context

The v2 API uses Braintrust for tracing. A single top-level span wraps each agent execution. The SDK (`setup_claude_agent_sdk`) also auto-creates child spans for LLM calls and tool executions, but these are opaque to us.

References:
- `server/docs/BRAINTRUST_TRACING.md` — Braintrust API surface and current implementation details
- `docs/plans/trace_6614a94e.json` — Real trace export for reference (DELETE BEFORE PR)

## Agent Flow

Two endpoints feed into the same agent: `/api/v2/chat/stream` (views.py) and `/api/v2/chat/anthropic` (anthropic_views.py).

**View layer** authenticates the request, creates/gets a session, loads any conversation summary, saves the user message to DB, then calls `agent.send_message()`. After the agent returns, the view logs the response to DB via `finalize_success()` and returns it to the client. The client can send an `X-Session-ID` header to indicate a multi-turn conversation; without it, an ephemeral session is created.

**Agent service** (`claude_service.py`) wraps execution in a `@traced(name="chat-agent", type="llm")` span. Inside, it fetches the core prompt from Braintrust's prompt registry (cached via PromptService), builds the system prompt (appending conversation summary if provided), logs input and metadata to the span, then hands off to the Claude Agent SDK. The SDK manages the actual LLM calls and dispatches tool calls to our handlers. Each tool handler calls `tool_executor.execute()`, records results to a list, and emits progress events. After the SDK finishes, the agent returns an `AgentResponse` with the final text, tool call records, latency, and trace_id.

**Key files:**
- `server/chat/V2/agent/claude_service.py` — agent service, span creation, tool handlers, `MessageContext`
- `server/chat/V2/views.py` — streaming endpoint, TracedThreadPoolExecutor
- `server/chat/V2/anthropic_views.py` — Anthropic-compatible endpoint
- `server/chat/V2/logging/turn_logging_service.py` — DB logging
- `server/chat/V2/prompts/prompt_service.py` — Braintrust prompt fetching
- `server/chat/V2/prompts/prompt_fragments.py` — LLM-facing text fragments and system prompt composition

## Current Span Structure

The single `chat-agent` span logs:
- **input**: `{message: last_user_message}`, plus `page_url` when the streaming endpoint provides one
- **metadata**: `core_prompt_id`, `core_prompt_version`, `core_prompt_in_options`, `summary_included`, and optionally `conversation_summary`

Phase 1 added output, metrics (latency_ms, tool_count), metadata (session_id, model), error tracking, and summary to the span.

## Trace Analysis (from trace_6614a94e)

Real trace of a 52s query with 9 tool calls. Actual span hierarchy:

```
chat-agent (root, our @traced span — type: llm)
└── Claude Agent (SDK task span — type: task)
    └── [intermediate span]
        ├── anthropic.messages.create ×13 (LLM calls)
        ├── text_search ×3
        ├── get_text ×5
        └── english_semantic_search ×1
```

Key findings:
- SDK tool spans already log tool input/output in `{content, is_error}` format with start/end timestamps. Less opaque than assumed.
- SDK LLM spans log the full message array (growing each turn) and assistant response.
- All 22 child spans are flat siblings under one intermediate parent — no meaningful nesting.
- `@traced` auto-captures the `AgentResponse` return value as span output, overwriting any explicit `span.log(output=...)`. Metrics merge correctly.
- `llm_calls` was hardcoded to 1 despite 13 actual LLM calls (now fixed to None).

## Remaining Problems

1. ~~llm_calls hardcoded to 1~~ → Now `None` (can't count SDK-internal calls)
2. **SDK spans are adequate but not controllable** — tool spans have input/output but we can't add custom fields
3. **No token tracking** — not in any span
4. **Prompt loading not traced** — can't see cache hit vs Braintrust fetch latency

## Proposed Changes

### Phase 1: Log missing data to the existing span

This is the lowest-effort, highest-impact work. All changes happen in `_send_message_inner()` in `claude_service.py`.

**Completed:** Introduced `MessageContext` to carry view-layer context (page URL, summary, session ID) into the agent service. Page context and summary text are now composed into the system prompt via `build_system_prompt()` in `prompt_fragments.py`, and the raw message and page URL are logged as separate fields on the span input. Both endpoints pass a `MessageContext` instead of individual kwargs.

**Completed:** After the agent finishes, the span now logs output (response text), metrics (latency_ms, tool_count), and metadata (session_id, model). Summary text is logged as span input alongside message and page_url. On exceptions, error status and message are logged to the span before re-raising. Phase 1 is complete.

### Phase 2: Improve span structure

The trace analysis shows the SDK already creates decent tool and LLM child spans. The original plan to create our own tool spans would duplicate what the SDK provides. Revised scope:

1. **Log prompt loading as a note** — record cache hit/miss and fetch latency in span metadata rather than a separate span (low effort, useful for debugging slow starts).

2. **Evaluate whether view-layer span is worthwhile** — the view layer handles auth, DB writes, and response formatting. These are fast operations (~ms). Moving the top-level span there would add coverage but the interesting work is all in the agent. Log as a note: not worth the complexity for now.

3. **Focus on what the SDK doesn't give us** — the SDK spans are adequate for tool/LLM visibility. The real gaps are token tracking (Phase 3) and prompt metadata, not span structure.

### Phase 3: Token tracking

Extract token counts from the SDK response. Currently not captured anywhere. This requires investigation into what the Claude Agent SDK exposes after `query()` completes.

## Status

- [x] Document current state (`server/docs/BRAINTRUST_TRACING.md`)
- [x] Map agent flow
- [x] Introduce `MessageContext` and `prompt_fragments.py` (Phase 1 prerequisite)
- [x] Add session_id to `MessageContext`
- [x] Log output, metrics, and errors to span
- [x] Fix llm_calls (was hardcoded to 1, now None)
- [x] Remove redundant output logging (overwritten by @traced)
- [x] Analyze real trace, document findings
- [ ] Agree on Phase 2 scope (revised — SDK spans are adequate, less work needed)
- [ ] Add prompt loading metadata (cache hit/miss, fetch latency)
- [ ] Phase 3: Token tracking
