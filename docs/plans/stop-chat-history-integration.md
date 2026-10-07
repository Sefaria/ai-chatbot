# Stop control on Chat History main

Base: e305242. Reuses Steve Kaplan's feature/sc-42230/1 (58e4627).

## Agreed behavior

- Stop replaces Send immediately, with a disabled waiting input.
- Preserve the prompt, location and visible topics; remove thinking; do not scroll to the notice.
- Restore the prompt/cursor to its own conversation when cancellation finishes.
- Keep the notice when reopening; remove it on the next prompt in that conversation, retaining topics.
- A fully validated answer returned before cancellation acknowledgement wins the race.
- Cancellation requests are `cancelling`; only acknowledged cancellation is `cancelled`.

## Implementation

- [x] Port authenticated database-backed cancellation and SDK checkpoints.
- [x] Make silent SDK receive waits interruptible and prioritize a ready terminal result.
- [x] Serialize cancel requests with final response persistence; protect early requests from running/heartbeat overwrites.
- [x] Return terminal cancellation from recovery.
- [x] Track active requests, stop state, progress and restored drafts by conversation.
- [x] Normalize history/cache notices from durable user-message state.
- [x] Integrate Figma stop icon, typography, waiting copy and delayed spinner into main's composer.
- [x] Add migration 0013 depending on main's existing 0012 (do not merge the old migration branch).
- [x] Add focused backend and JavaScript tests, build and browser checks.

## Validation

Frontend: production build succeeds; 6 Node tests pass. Browser checks with a local simulated API cover stop after topics, prompt/cursor restoration, notice on reload/reopen, notice removal with preserved topics, concurrent chats, completion winning, cancellation failure/retry availability, and Hebrew placement/copy.

Backend: 121 tests pass across streaming, SDK, serialization, models and history. One history search test fails identically on unchanged e305242: `test_conversation_search_matches_title_message_and_appetizer`. The new cancellation/history tests pass. SQLite tests use `--nomigrations` because main's 0012 contains PostgreSQL-only SQL. `makemigrations --check --dry-run` reports no changes; PostgreSQL migration execution is not verified.

## Limits and follow-up

No live LLM calls or deployment were performed. Cancellation closes the SDK client at a checkpoint or during a quiet response wait; already in-flight synchronous auxiliary work (guardrail/router/appetizer calls) may finish before its next checkpoint and is not promised to have zero additional provider cost. The independent appetizer cannot publish after a noticed stop request. Production latency and multi-process PostgreSQL behavior still need staging QA.

The full Chat History Google document was not available. The supplied Stop spec and the approved notice policy govern this integration.
