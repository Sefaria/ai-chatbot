# Listening mode

## Implemented

- [x] Svelte listening panel with browser capability detection, explicit start/stop,
  microphone state, dismissible question cards, and no focus/scroll takeover.
- [x] Five-second, non-overlapping HTTP analysis requests with finalized speech,
  sequence acknowledgments, bounded offline buffering, request replay, and backoff.
- [x] Tool-free Haiku detection followed by Sonnet reformulation. Evidence must
  include new speech. Both calls request schema-constrained JSON and validate it locally. Incomplete output, unsupported decisions, ungrounded evidence,
  and overlong questions fail closed. Positive detection is checkpointed for retries.
- [x] Shared Django database state, compare-and-swap leases, cancellation guards,
  idle/hard expiry, bounded recent suggestions and idempotency history.
- [x] Suggestions enter the existing guarded chat flow only on click. Stable message
  IDs use its normal duplicate/recovery handling. A typed draft is preserved.
- [x] Capture page URL with each speech segment and forward the suggestion's URL
  to chat; navigating to another source does not change the question's context.
- [x] Backend, controller, and browser tests; cleanup command and configuration.

## Architecture decisions

Use the existing PostgreSQL database; this application has no shared Redis setup.
No queue, WebSockets, audio storage, new transcription vendor, or answering tools
are introduced. The listening endpoint is independent of the existing SSE chat API.

The first rollout requires login. Anonymous users retain their normal chat path;
ambient model calls cannot be used to bypass anonymous free-answer accounting.
Only browsers exposing SpeechRecognition/webkitSpeechRecognition display the control.
An unplanned recognition end stops the session visibly rather than automatically
restarting the microphone. Closing the panel, opening history, switching conversations,
or removing the widget stops listening and clears pending suggestions/transcript.

Only finalized recognition results are sent. A request accepts up to 30 segments
of 2,000 characters each, but the total server/client transcript buffer is capped at
18,000 characters. Each detector pass evaluates up to 6,000 new characters plus
three earlier segments. Acknowledgment and analysis cursors are distinct; an empty
batch drains already-received backlog. Three overlapping segments preserve questions
spanning a boundary. Recent questions (including dismissed ones) provide semantic
deduplication context; exact duplicates are also suppressed by the application.
One question per analysis pass is the deliberate MVP behavior.

Sessions expire after 30 idle minutes or one hour total. Six starts per user per
hour and 360 analysis attempts per session bound cost; analysis is at most once per
five seconds per session. The start count is an application budget, not a strict
transactional quota under concurrent session creation. Authenticated internal rollout
plus provider spending limits remain appropriate. Do not expose anonymous listening
without an identity-resistant quota design.

A 40-second lease spans two model calls with 12-second provider timeouts and no SDK
retries. Calls pass no `temperature`: anthropic>=2 rejects sampling params with a
TypeError (structured output via `output_config` keeps responses parseable). Stop clears the lease and payload; old workers cannot publish. Client requests
time out after 35 seconds and retry with the same request ID. Recently completed
request IDs (20) are cached; replay with changed payload is rejected. Older replay
is not supported. Session state retains up to 30 suggestions, so semantic duplicate
suppression is bounded rather than a permanent history.

Suggestion status updates are best effort and cannot overwrite an analysis lease.
The UI hides a selected/dismissed card locally even if that update races a request.
A clicked card is handed to chat with a stable message ID; the chat's persisted
message and delivery/error UI are authoritative, not listening status metadata.
No full transcript enters a chat message, browser persistence, or application logs.
Model latency, token counts, and estimated model cost are logged without speech.
The model prompts live in the source module for a self-contained initial rollout.

## API

All mutations accept the existing encrypted `userId` body field or X-Api-Key token.

| Method | Path below `/api/v2/listening` | Behavior |
| --- | --- | --- |
| GET | `/sessions` | Public feature capability; no microphone/session creation |
| POST | `/sessions` | Create authenticated session, return session ID and cursors |
| POST | `/sessions/{uuid}/analyze` | `request_id`, `segments[{seq,text,page_url}]`; return cursors/cards |
| POST | `/sessions/{uuid}/stop` | Idempotently close and clear transcript/suggestions |
| PATCH | `/sessions/{uuid}/suggestions/{uuid}` | Record accepted/dismissed metadata |

400: invalid payload. 401: invalid/missing login. 404: unavailable feature or foreign
session. 409: lease, sequence, or idempotency conflict. 410: closed/expired session.
413: transcript backlog full. 429: cadence/session budget. 503: model unavailable.

## Rollout

- [ ] Validate real microphones, supported browsers, and mixed-language learning speech.
- [ ] Schedule cleanup and enable the flag on an internal deployment.
- [ ] Review detection quality and cost before wider release.

1. Apply migrations (`python manage.py migrate`).
2. Schedule `python manage.py cleanup_listening` every five minutes in the same
   environment/database as the API. It clears expired payloads and deletes session
   rows older than two hours, preserving hourly start budgets. Logical expiry alone
   does not physically remove idle rows, so this job is required before enabling.
3. Set `LISTENING_ENABLED=true` on the internal deployment. Default is false.
4. Optionally override `LISTENING_DETECT_MODEL` and `LISTENING_REWORD_MODEL`.
   Defaults are the application's Haiku 4.5 and Sonnet 4.6 model identifiers.
5. Validate actual microphones and English/Hebrew/Aramaic code switching on launch
   browsers. Browser recognition may process audio remotely; the UI discloses this.
6. Review false positives on quoted source questions, implicit inquiry recall,
   question fidelity, latency, and spending before wider release.

Disable `LISTENING_ENABLED` to stop new sessions/analysis. Stop/status cleanup remains
available. No live provider calls or physical microphone testing are part of mocked
CI tests. Hebrew copy is handled through the existing Weblate workflow; new keys
initially fall back to English.

## Validation

- `cd server && ../.venv/bin/pytest chat/tests/test_listening.py --nomigrations`
- `node --test tests/*.test.js`
- `npm run build`
- `npm run test:e2e -- e2e/listening.spec.js`

Local SQLite requires `--nomigrations` because an existing migration uses PostgreSQL
SQL. CI uses PostgreSQL and exercises migrations normally.

Validation on the implementation branch: 14 listening backend tests and 13 Node
controller/API regression tests pass; production build, Ruff, and migration drift
checks pass. Mocked browser checks passed through the connected Playwright browser
for suggestion/click/draft/source-context behavior, dismissal, stop-on-close, and
the disabled flag. The standalone Chromium/WebKit runners could not launch in the
local execution profile; the checked-in cross-browser specs remain to run in CI or
an unrestricted developer environment. The full backend suite was blocked by
approval review after attempting Braintrust network access; it was not rerun.
