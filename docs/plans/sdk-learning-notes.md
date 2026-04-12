# Claude Agent SDK — Learning Notes

This is a working document from an ongoing learning process where we (Daniel + Claude) are
walking through SLA's usage of the Claude Agent SDK using the actual codebase as curriculum.
Cleanup commits on this branch are a side effect, not the goal.

## Status

- Phase 1 (what we have) — reviewed sections 1-2 of 4; sections 3-4 queued for next session
- Phase 2 (gaps & recommendations) — not started

## What We've Learned

### SDK entry point and architecture
- All SDK usage flows through a single path: `ClaudeAgentService` -> `TurnOrchestrator` -> `ClaudeSDKRunner`
- The SDK is used as a "dumb executor" — routing, guardrails, and prompt selection all happen before the SDK runs
- Pre-SDK pipeline: guardrail (Haiku, direct API) -> router (Haiku, direct API) -> prompt assembly -> SDK execution

### Tools vs MCP clarification
- We define tools (JSON schemas) and implement handlers (async Python functions)
- `create_sdk_mcp_server()` is just the SDK's internal transport — MCP is plumbing, not an architectural choice
- The `mcp__sefaria__` naming convention in `allowed_tools` is an SDK requirement, not us choosing MCP
- 16 Sefaria tools registered, all built-in Claude tools implicitly blocked via explicit allowlist

### Guardrail architecture
- The "no psak" guardrail is a pre-agent LLM judge, not an SDK hook
- It only checks user input; there is no output-side guardrail
- If Claude produces halakhic guidance in its response, nothing catches it

### Options and configuration
- `permission_mode: "bypassPermissions"` — correct for production (only our tools are available)
- `temperature` was 0.7, changed to 0 (deterministic) — may revisit if responses feel too robotic
- `max_iterations` was accepted but never wired to `max_turns` — removed
- `continue_conversation: False` — each turn is independent
- No hooks, no subagents, no structured output on the SDK client, no `setting_sources`

## Cleanups Done

1. **Removed SDK introspection** — `_supports_option()` in `sdk_options_builder.py` was checking
   whether the installed SDK accepted each constructor parameter. This was defensive code from
   early SDK flux. Now we pass options directly and get clear errors if something breaks.

2. **Removed dead `max_iterations` parameter** — accepted by `ClaudeAgentService.__init__()` but
   never passed to the SDK. The SDK equivalent (`max_turns`) is still not set, which is a
   separate issue to address.

3. **Set temperature to 0** — was 0.7. Removed the `temperature` parameter from the service
   constructor since it's now a fixed value in the builder. Matches guardrail/router pattern
   (both use temp=0). May revisit if response quality suffers.

4. **Removed `inspect` import** — no longer needed after introspection removal.

5. **Wired `max_turns=10`** — the old `max_iterations` param was never connected to anything.
   Now passed through as `max_turns` to `ClaudeAgentOptions`, which caps the SDK's agentic loop
   at 10 turns (tool calls + responses). Prevents runaway loops and bounds cost/latency.

## Still To Investigate (Phase 1 sections 3-4)

- Hooks for guardrails (vs current pre-agent LLM judge pattern)
- Whether `max_turns` should be set explicitly
- SDK features we're not using: subagents, structured output, `can_use_tool`, `setting_sources`
- Whether the over-abstraction (11-param TurnOrchestrator constructor, DI everywhere) is worth addressing

## What We Don't Know Yet

- Exact feature set of SDK 0.1.27 vs latest — loose pin `>=0.1.0` could surprise us
- Whether hooks could replace or supplement the pre-agent guardrail for output-side checks
- Whether subagents would help for bounded subtasks (parallel text retrieval, per-commentary research)
- Cost implications of the current setup (no `max_turns` limit, no output-side cost controls)

## Session Notes

- Daniel is the engineer who works on SLA. Prefers concrete, code-cited explanations over generic SDK tutorials.
- Reads the walkthrough in sections, not all at once — reviewed 1-2, will read 3-4 next session.
- Wants criticism of the code, not hedging. Confidence levels appreciated.
