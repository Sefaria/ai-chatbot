# Stop control on Chat History main

Base: e305242. Reuses Steve Kaplan's feature/sc-42230/1 (58e4627).

## Agreed behavior

- Stop replaces Send immediately, with a disabled waiting input.
- Preserve the prompt, location and visible topics; remove thinking; reveal the notice after restoring the composer (design revision, 12 October).
- Restore the prompt/cursor to its own conversation when cancellation finishes.
- Keep the notice when reopening; remove it on the next prompt in that conversation, retaining topics.
- A committed answer wins the race. An accepted stop atomically fences the turn against later success/error persistence.
- Stop returns durable `cancelled` immediately; shutdown and telemetry continue in the background. Legacy `cancelling` responses remain supported by the frontend.

## Implementation

- [x] Port authenticated database-backed cancellation and SDK checkpoints.
- [x] Make silent SDK receive waits interruptible and prioritize a ready terminal result.
- [x] Serialize cancel requests with final response persistence; protect early requests from running/heartbeat overwrites.
- [x] Return terminal cancellation from recovery.
- [x] Track active requests, stop state, progress and restored drafts by conversation.
- [x] Normalize history/cache notices from durable user-message state.
- [x] Integrate Figma stop icon, typography, waiting copy into main's composer; the pending spinner was removed in the 12 October design revision.
- [x] Add migration 0013 depending on main's existing 0012 (do not merge the old migration branch).
- [x] Add focused backend and JavaScript tests, build and browser checks.

## Validation

Frontend: production build succeeds; 6 Node tests pass. Browser checks with a local simulated API cover stop after topics, prompt/cursor restoration, notice on reload/reopen, notice removal with preserved topics, concurrent chats, completion winning, cancellation failure/retry availability, and Hebrew placement/copy.

Backend: 121 tests pass across streaming, SDK, serialization, models and history. One history search test fails identically on unchanged e305242: `test_conversation_search_matches_title_message_and_appetizer`. The new cancellation/history tests pass. SQLite tests use `--nomigrations` because main's 0012 contains PostgreSQL-only SQL. `makemigrations --check --dry-run` reports no changes; PostgreSQL migration execution is not verified.

## Limits and follow-up

No live LLM calls or deployment were performed. Cancellation closes the SDK client at a checkpoint or during a quiet response wait; already in-flight synchronous auxiliary work (guardrail/router/appetizer calls) may finish before its next checkpoint and is not promised to have zero additional provider cost. The independent appetizer cannot publish after a noticed stop request. Production latency and multi-process PostgreSQL behavior still need staging QA.

The full Chat History Google document was not available. The supplied Stop spec and the approved notice policy govern this integration.

## Stop latency follow-up — 11 October 2026

- [x] Atomically mark accepted stops cancelled under the same row lock used by response persistence. Guard both late success and late error writes.
- [x] End the SSE loop on the shared stop flag without waiting for worker completion or Braintrust flush.
- [x] Release the frontend on a confirmed cancel response, abort its stream, and ignore the old promise outcome. Preserve the existing stopped-turn/history rendering path and failure behavior.
- [x] Test a blocked worker and blocked log flush independently: Stop completes and a second prompt in the same session completes before cleanup is released.
- [x] Test Stop landing between the initial result check and final success/error persistence, plus already-completed cancellation, idempotence, and history recovery.

Results: 125 backend tests pass; the same pre-existing history search test fails. Eight frontend tests pass and production build succeeds. Local backend tests measured about 0.26 seconds to terminal SSE with cleanup still blocked. Browser verification with a simulated API returning cancellation immediately but delaying terminal SSE for 10 seconds restored the question, enabled Send and preserved topics in 296 ms (automation measurement, not a production performance guarantee).

The outcome boundary is committed response persistence, not the moment the provider generates its last token. PostgreSQL cross-process row-lock contention and live provider cancellation still need staging validation. This change shortens the UI wait; it does not make physical worker shutdown or logging faster, and old work can consume resources briefly while shutting down.

## Design revision — 12 October 2026

Reference: PR #246 at e43cedc, run locally with a simulated API. Treated as a behavior specification; no merge or cherry-pick.

- [x] Keep the square Stop icon, disabled, while confirmation is pending. Remove spinner state/timer/styles.
- [x] Use the existing production disabled-input background.
- [x] Grow the composer to three lines, scroll beyond that, and reset its height when sent. Measure after text binding updates.
- [x] Restore the prompt at its measured height, caret at the end, input scrolled to its end. Then reveal the Stop notice at the bottom of the canvas.
- [x] Use existing spacing, type, color and radius variables for Stop and its notices. Use hover only for hover-capable pointers and retain a pressed state.
- [x] Keep notice dismissal on the next prompt and preserve the fast-stop confirmation path.

Future mobile layout, keyboard/tooltip changes, anonymous authentication, quotas and migrations remain out of scope. Backend code is unchanged.

Validation: reference and implementation both measured 80.8px for the capped composer and restored the five-line prompt at cursor position 114/114 with internal scrolling at the bottom. Verified single-line reset (42px), retained Stop icon during delayed confirmation, notice visible after scrolling to the top of a long conversation, notice dismissal on resubmission with retained topics, completed-answer precedence, and retry after cancellation failure. Eight Node tests and production build pass. Browser checks use a simulated API, not production LLM calls. A simulation pagination bug initially returned duplicate messages; corrected the test server and reran the Stop/resubmit/completion/error flow with no browser errors.
