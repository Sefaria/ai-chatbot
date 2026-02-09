# Tracing Improvements Plan

Goal: Improve Braintrust span logging in the v2 API so traces are useful for debugging and observability.

## Context

The v2 API uses Braintrust for tracing. A single top-level span wraps each agent execution. The SDK (`setup_claude_agent_sdk`) also auto-creates child spans for LLM calls and tool executions, but these are opaque to us.

References:
- `server/docs/BRAINTRUST_TRACING.md` — Braintrust API surface and current implementation details

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
3. **No token tracking on root span or DB** — Braintrust wrapper handles LLM child spans (cost estimation works), but our `chat-agent` span has no token metrics, DB fields are empty, and Anthropic API returns 0
4. ~~Prompt loading not traced~~ → Added Python logging (cache/fetch/latency)

## Proposed Changes

### Phase 1: Log missing data to the existing span

This is the lowest-effort, highest-impact work. All changes happen in `_send_message_inner()` in `claude_service.py`.

**Completed:** Introduced `MessageContext` to carry view-layer context (page URL, summary, session ID) into the agent service. Page context and summary text are now composed into the system prompt via `build_system_prompt()` in `prompt_fragments.py`, and the raw message and page URL are logged as separate fields on the span input. Both endpoints pass a `MessageContext` instead of individual kwargs.

**Completed:** After the agent finishes, the span now logs output (response text), metrics (latency_ms, tool_count), and metadata (session_id, model). Summary text is logged as span input alongside message and page_url. On exceptions, error status and message are logged to the span before re-raising. Phase 1 is complete.

### Phase 2: Improve span structure

The trace analysis shows the SDK already creates decent tool and LLM child spans. The original plan to create our own tool spans would duplicate what the SDK provides. Revised scope based on "keep it simple" principle:

**Decided against tracing to Braintrust:**
- Prompt cache hit/miss — not worth a span or metadata field. Added Python logging instead (`logger.debug` for cache hits, `logger.info` for fetches with latency).
- View-layer span — auth, DB writes, and response formatting are fast (~ms). The interesting work is all inside the agent. Not worth the added complexity.
- Our own tool child spans — SDK already creates these with input, output, and timestamps. Duplicating would add noise.

**Logged via Python logging instead:**
- Prompt loading: cache hit/miss, fetch latency (in `prompt_service.py`)

**Remaining:** SDK spans are adequate for tool/LLM visibility. The real gap is token tracking (Phase 3).

### Phase 3: Token tracking

**Goal:** Extract token usage from the SDK and propagate it to the Braintrust span, DB, and API response.

**Current state:** The Braintrust wrapper already handles token tracking on the LLM child spans — each `anthropic.messages.create` span gets model metadata and token metrics, and Braintrust aggregates these upward in the UI for cost estimation (confirmed working: screenshot shows $0.141 estimated cost). However, our root `chat-agent` span has no token metrics of its own (only `start`/`end`), the DB token fields are never populated, and the Anthropic-compatible API returns `usage: {input_tokens: 0, output_tokens: 0}`.

**How it works:** The SDK's `receive_response()` yields messages ending with a `ResultMessage` (from `claude_agent_sdk.types`). This has `usage: dict[str, Any] | None` containing standard Anthropic fields (`input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`) and `total_cost_usd: float | None`. The Braintrust wrapper intercepts this and logs normalized metrics (`prompt_tokens`, `completion_tokens`, `tokens`, `prompt_cached_tokens`, `prompt_cache_creation_tokens`) to the last LLM child span via `llm_tracker.log_usage()`. We currently ignore the `ResultMessage` entirely — `_extract_text_from_message` returns empty string for it.

**Approach:** Capture the `ResultMessage` as it flows through the existing message loop in `_send_message_inner`. Extract the usage dict and cost, add them to `AgentResponse`, and let the existing plumbing carry them to the span, DB, and API response.

**Changes:**

1. **Capture `ResultMessage`** in `_send_message_inner` (`claude_service.py`): In the `async for message in client.receive_response()` loop, check `isinstance(message, ResultMessage)`. Store its `usage` dict and `total_cost_usd`. Import `ResultMessage` from `claude_agent_sdk.types`.

2. **Add token fields to `AgentResponse`** (`claude_service.py`): Add `input_tokens: int | None = None`, `output_tokens: int | None = None`, `cache_creation_tokens: int | None = None`, `cache_read_tokens: int | None = None`, `cost_usd: float | None = None`. Populate from the captured `ResultMessage`.

3. **Log tokens to the Braintrust span** (`claude_service.py`): In the existing `span.log(metrics={...})` call, add the token fields using Braintrust's standard metric names: `prompt_tokens`, `completion_tokens`, `tokens`, `prompt_cached_tokens`, `prompt_cache_creation_tokens`. This makes the root span queryable by tokens, not just relying on UI aggregation. Convention per Braintrust docs: `prompt_tokens` should include both cached and cache creation tokens.

4. **Persist to DB** (`turn_logging_service.py`): In `finalize_success`, pass token fields from `AgentResponse` to `ChatMessage.objects.create()`. Update session aggregates (`total_input_tokens`, `total_output_tokens`), add them to `save(update_fields=...)`.

5. **Include in stats/API response** (`turn_logging_service.py`): Add `inputTokens` and `outputTokens` to `build_stats()`. The Anthropic-compatible endpoint already reads these from stats — it will start returning real values instead of 0.

6. **Tests**: Mock `ResultMessage` with usage data, verify propagation to `AgentResponse`, span metrics, and DB record.

7. **Count LLM calls**: Import `AssistantMessage` from `claude_agent_sdk.types`. In the same `receive_response()` loop, increment a counter on each `AssistantMessage` (each one represents one LLM call, confirmed by how the Braintrust wrapper creates one LLM span per `AssistantMessage`). Set `llm_calls` on `AgentResponse` and log as a span metric. This replaces the previous `None` value.

8. **Cost tracking**: `ResultMessage.total_cost_usd` is calculated server-side by the Anthropic API (not from local pricing tables), making it the authoritative cost figure. Braintrust's `estimated_cost` is only in the UI — not returned in the fetch API. So we persist `cost_usd` to `ChatMessage`, aggregate `total_cost_usd` on `ChatSession`, include `costUsd` in `build_stats`, and log `cost_usd` as a span metric for Braintrust queryability.

**What we're NOT doing:**
- Not querying Braintrust API for child span metrics — the `ResultMessage` gives us the aggregate for the entire turn.
- Not changing the Braintrust wrapper — it already handles LLM child spans correctly.
- Not manually logging `estimated_cost` — Braintrust auto-aggregates this from child LLM spans in the UI. Our `cost_usd` metric uses a distinct name to avoid double-counting.

## Status — COMPLETE (archived 2026-02-09)

All phases implemented and verified against live Braintrust traces.

- [x] Document current state (`server/docs/BRAINTRUST_TRACING.md`)
- [x] Map agent flow
- [x] Introduce `MessageContext` and `prompt_fragments.py` (Phase 1 prerequisite)
- [x] Add session_id to `MessageContext`
- [x] Log output, metrics, and errors to span
- [x] Fix llm_calls (was hardcoded to 1, now None — can't count SDK-internal calls)
- [x] Remove redundant output logging (overwritten by @traced)
- [x] Analyze real trace, document findings
- [x] Phase 2 scope agreed: SDK spans are adequate, keep it simple
- [x] Add Python logging for prompt loading (cache/fetch/latency)
- [x] Remove local prompt fallback (cache or fetch only, errors surface)
- [x] Phase 3: Capture `ResultMessage` in message loop
- [x] Phase 3: Add token fields to `AgentResponse`
- [x] Phase 3: Log token metrics to Braintrust span
- [x] Phase 3: Persist tokens to DB (`ChatMessage` + `ChatSession` aggregates)
- [x] Phase 3: Include tokens in `build_stats` / API response
- [x] Phase 3: Add tests
- [x] Extract real llm_calls count from SDK (count `AssistantMessage` instances)
- [x] Persist `cost_usd` to DB (`ChatMessage` + `ChatSession.total_cost_usd`)
- [x] Log `cost_usd` to Braintrust span metrics
- [x] Include `costUsd` in `build_stats` / API response
- [~] Integration test for `ResultMessage` capture — deferred (no official SDK mock/test utilities)

Deferred items are documented, not blocking.
