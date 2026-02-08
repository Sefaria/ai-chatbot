# Tracing Improvements Plan

Goal: Improve Braintrust span logging in the v2 API so traces are useful for debugging and observability.

## Context

The v2 API uses Braintrust for tracing. A single top-level span wraps each agent execution. The SDK (`setup_claude_agent_sdk`) also auto-creates child spans for LLM calls and tool executions, but these are opaque to us.

Reference: `server/docs/BRAINTRUST_TRACING.md` has the full Braintrust API surface and current implementation details.

## Agent Flow

Two endpoints feed into the same agent: `/api/v2/chat/stream` (views.py) and `/api/v2/chat/anthropic` (anthropic_views.py).

**View layer** authenticates the request, creates/gets a session, loads any conversation summary, saves the user message to DB, then calls `agent.send_message()`. After the agent returns, the view logs the response to DB via `finalize_success()` and returns it to the client. The client can send an `X-Session-ID` header to indicate a multi-turn conversation; without it, an ephemeral session is created.

**Agent service** (`claude_service.py`) wraps execution in a `@traced(name="chat-agent", type="llm")` span. Inside, it fetches the core prompt from Braintrust's prompt registry (cached via PromptService), builds the system prompt (appending conversation summary if provided), logs input and metadata to the span, then hands off to the Claude Agent SDK. The SDK manages the actual LLM calls and dispatches tool calls to our handlers. Each tool handler calls `tool_executor.execute()`, records results to a list, and emits progress events. After the SDK finishes, the agent returns an `AgentResponse` with the final text, tool call records, latency, and trace_id.

**Key files:**
- `server/chat/V2/agent/claude_service.py` — agent service, span creation, tool handlers
- `server/chat/V2/views.py` — streaming endpoint, TracedThreadPoolExecutor
- `server/chat/V2/anthropic_views.py` — Anthropic-compatible endpoint
- `server/chat/V2/logging/turn_logging_service.py` — DB logging
- `server/chat/V2/prompts/prompt_service.py` — Braintrust prompt fetching

## Current Span Structure

The single `chat-agent` span logs:
- **input**: `{message: last_user_message}`
- **metadata**: `core_prompt_id`, `core_prompt_version`, `core_prompt_in_options`, `summary_included`, and optionally `conversation_summary`

It does **not** log output, metrics, session_id, model, status, or errors.

SDK auto-creates child spans for LLM calls and tool executions, but we have no control over what they contain.

## Problems

1. **No output on span** — we can't see the agent's response in Braintrust
2. **No metrics on span** — latency, tool count not in Braintrust (only in DB)
3. **llm_calls hardcoded to 1** — `AgentResponse.llm_calls` is always 1 regardless of actual SDK behavior
4. **No session context** — can't correlate traces across a conversation
5. **No error tracking** — failures aren't captured in the span
6. **SDK spans are opaque** — we rely on `setup_claude_agent_sdk` for child spans with no visibility into what's logged

## Proposed Changes

### Phase 1: Log missing data to the existing span

This is the lowest-effort, highest-impact work. All changes happen in `_send_message_inner()` in `claude_service.py`.

After the agent finishes (where `AgentResponse` is built), log output and metrics to the current Braintrust span: the response text, tool calls list, latency, and tool count. Also add `session_id` and `model` to metadata — `session_id` will need to be passed from the view layer into `send_message()`. On exceptions, log error status and message to the span before re-raising.

### Phase 2: Improve span structure

Create explicit child spans for tool executions inside the tool handler, so each tool call gets its own span with input, output, latency, and error status. This gives us control instead of relying on SDK auto-spans.

Consider adding a span for prompt loading to track cache hits vs Braintrust fetches.

Consider moving the top-level span from the agent to the view layer so the trace covers the full request lifecycle (auth, DB writes, etc.), not just the agent execution.

### Phase 3: Token tracking

Extract token counts from the SDK response. Currently not captured anywhere. This requires investigation into what the Claude Agent SDK exposes after `query()` completes.

## Status

- [x] Document current state (`server/docs/BRAINTRUST_TRACING.md`)
- [x] Map agent flow
- [ ] Agree on Phase 1 scope
- [ ] Implement Phase 1
- [ ] Agree on Phase 2 scope
- [ ] Implement Phase 2
