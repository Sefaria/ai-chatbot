# Native Multi-Turn Conversations via SDK Session Resume

**Status:** In progress (steps 1, 2, 4 done; behind `NATIVE_MULTI_TURN_ENABLED`, default off)
**Date:** 2026-10-11
**Scope:** Replace summary-based multi-turn context with Claude Agent SDK session resume,
backed by a Postgres transcript store, and let the CLI's built-in auto-compaction manage
context size.

---

## 1. Problem

Every turn starts a fresh `ClaudeSDKClient` subprocess with only the latest user message
(`server/chat/V2/views.py`, `conversation = [ConversationMessage(role="user", ...)]`) and
`continue_conversation=False` (`sdk_options_builder.py`). The only cross-turn memory is the
`SummaryService` JSON blob, built from the first 1000 chars of each message and injected into
the system prompt. Follow-ups that depend on earlier answers or tool results ("what does Rashi
say about *that*?") lose context, and every turn pays for an extra summarization LLM call.

## 2. Approach

`claude-agent-sdk` (>= 0.2.x) provides:

- `ClaudeAgentOptions.resume` / `fork_session` — resume a prior session transcript.
- `ClaudeAgentOptions.session_store` — a `SessionStore` adapter (`append`, `load`) that
  mirrors transcripts to external storage and materializes them before resume, so any pod can
  resume any session.
- CLI auto-compaction (tunable via `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`,
  `CLAUDE_CODE_AUTO_COMPACT_WINDOW`) and a `PreCompact` hook.

## 3. Steps

### Step 1 — Postgres `SessionStore` ✅
- `SdkTranscriptEntry` model (`chat/models.py`, migration `0013`): one row per transcript
  line, keyed by `(project_key, session_id, subpath)`, `uuid` as idempotency key via a partial
  unique constraint.
- `PostgresSessionStore` (`chat/V2/agent/session_store.py`): implements `append`, `load`,
  `list_sessions`, `delete`, `list_subkeys`. ORM calls run via `sync_to_async`.
- ORM work closes its connection after each call (`close_old_connections`): the SDK calls
  the store outside any request cycle, so Django would otherwise never release it.
- Verified with the SDK's `run_session_store_conformance` suite
  (`chat/tests/test_session_store.py`). `list_session_summaries` is not implemented (optional;
  only used by SDK session listing, which we don't need).
- `claude-agent-sdk` floor raised to `0.2.159` (first version tested with `session_store`).
- Not wired into the agent yet — no behavior change.

### Step 2 — Track the conversation head ✅
- `ChatSession.sdk_session_id` (nullable, migration `0014`).
- `SDKRunResult.session_id` read from `ResultMessage.session_id`, surfaced as
  `AgentResponse.sdk_session_id` and saved in `update_session_success` (success path only, so
  errored and guardrail-blocked turns never move the head).
- `views.py` passes `MessageContext.sdk_resume_session_id` and, when
  `NATIVE_MULTI_TURN_ENABLED` and not a load test, `get_agent_service(session_store=...)`.
- `SDKOptionsBuilder.build(session_store=, resume=)` sets `session_store`, `resume` and pins
  `cwd` to `BASE_DIR` (the store's `project_key` derives from cwd; prod already runs there).
- **Decision: resume in place, no `fork_session`.** Forking likely copies the full transcript
  into a new session each turn (quadratic storage). The cost of resuming in place: a turn that
  fails mid-run leaves its partial entries in the transcript.
- **Decision: no `CLAUDE_CONFIG_DIR` override.** Resumed runs already materialize into an
  SDK-managed temp dir; first turns write to the default local dir, as they do today.
- **Fallback:** if a resumed run raises (missing/unreadable transcript), the orchestrator logs
  `sdk_resume_failed` and retries once as a fresh session.
- The summary is still injected into the system prompt during rollout, which keeps that
  fallback context-aware.
- Store ORM calls run with `thread_sensitive=False` so concurrent turns don't serialize on one
  thread.

### Step 3 — Send only the new message
- `build_turn_prompt` stops flattening history; `prompt_text` is the (router-rewritten) user
  message.
- Move `page_url` from the system prompt into the user message so the system prompt stays
  stable and prompt caching hits across turns.

### Step 4 — System prompt snapshot ✅
- Done with step 2 (only when a store is set). Pass the system prompt as `{"type": "custom", ..., "snapshot": False}` — otherwise a resumed
  session keeps its first-turn prompt and ignores the router's per-turn core prompt.

### Step 5 — Context management
- Rely on CLI auto-compaction; tune the threshold via subprocess env.
- If carried-over Sefaria tool results make turns too expensive, strip old `tool_result`
  content in `PostgresSessionStore.load()`.
- Optionally log compactions to Braintrust via the `PreCompact` hook.

### Step 6 — Cleanup and rollout
- ✅ Link-repair loop resumes from the turn's new session id and sends only the repair
  instruction (done in step 2).
- Sessions without `sdk_session_id` fall back to the summary path for their first turn.
- Keep `SummaryService` behind a setting for one release, then remove `summarization/`,
  summary fragments and summary columns.
- ✅ Load tests use no store.
- `/api/v2/chat/anthropic` keeps flattening its message array.

## 4. Open questions

- **Verify against the real CLI before enabling** (not possible in CI, no API key): a
  multi-turn conversation in staging with `NATIVE_MULTI_TURN_ENABLED=true` should show
  `sdk_resumed: true` in Braintrust from turn 2, a stable `ChatSession.sdk_session_id`, the
  model answering follow-ups from earlier turns, and the router's per-turn core prompt taking
  effect (snapshot off). Check auto-compaction on a long conversation.
- **Retention.** Transcripts contain user text; align deletion with `ChatSession.is_deleted`
  and any retention policy.
