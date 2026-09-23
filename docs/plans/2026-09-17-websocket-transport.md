# WebSocket Transport for Chat Streaming

**Status:** Proposed
**Date:** 2026-09-17
**Scope:** Replace browser-facing SSE streaming with a single persistent WebSocket, so the
server can push to the client outside of a request the client initiated.

---

## 1. Goal and non-goals

**Goal.** One long-lived, authenticated, bidirectional socket per open widget. Turn streaming
(progress, final message, errors) moves onto it, and the server gains the ability to push
unsolicited events to a connected client.

**Non-goals for this change**
- Token-level text streaming of the assistant answer (the agent still returns one final
  markdown blob; that is a separate change on the agent side).
- Removing the HTTP SSE endpoint. `POST /api/v2/chat/stream` stays as the machine-facing
  transport — three non-browser consumers depend on it (§3.4).
- Cross-pod fan-out for pushes originating in another process. Phase 2, designed for but not
  built here (§11).
- Any change to the agent, guardrail, router, appetizer, summarization, or Braintrust layers.

---

## 2. What exists today

| Piece | Location |
|---|---|
| SSE endpoint | `server/chat/V2/views.py:265` (`chat_stream_v2`) |
| SSE generator (turn lifecycle) | `server/chat/V2/views.py:423` (`generate_sse`) |
| Recovery endpoint | `server/chat/V2/views.py:809` (`chat_recover_v2`) |
| Client telemetry endpoint | `server/chat/V2/views.py:909` (`chat_client_event_v2`) |
| Browser SSE reader | `src/lib/api.js:266` (`sendMessageStream`) |
| Recovery poller | `src/lib/api.js:123` (`recoverStreamMessage`) |
| Widget call site | `src/components/LCChatbot.svelte:1059` |
| Event contract | `server/chat/V2/agent/contracts.py` (`AgentProgressUpdate`) |
| Serving | `Dockerfile:34` — `gunicorn chatbot_server.wsgi:application --worker-class gthread --threads 4` |

Per-turn mechanics today: the request thread runs `generate_sse()`; an appetizer thread and an
agent thread are spawned; both feed a bounded `queue.Queue` (maxsize 100); the request thread
drains it, writes `event: progress` frames, marks a DB heartbeat on every event, and emits a
terminal `event: message` or `event: error`. A 60s `queue.Empty` timeout emits an SSE comment
keepalive. The 4KB comment at the top of the stream exists purely to punch through proxy
buffering.

---

## 3. Constraints this design has to respect

### 3.1 WSGI cannot serve WebSockets
The app is WSGI (`server/chatbot_server/wsgi.py`, no `asgi.py`). WebSockets require ASGI. This is
the single largest piece of work and it touches the Dockerfile entrypoint and the k8s
deployment.

### 3.2 Concurrency today is 4 turns per pod
`--worker-class gthread --threads 4` with no `--workers` means **one worker, four threads** — a
turn occupies a request thread for its full duration (10–60s+). Under ASGI a socket costs an
asyncio task instead of a thread, but a turn still needs a worker thread because the turn
pipeline (Django ORM, Braintrust, `asyncio.run` on the agent) is blocking. So the ASGI move
**decouples connection count from turn concurrency** — sockets get cheap, turns do not. Turn
concurrency must be given an explicit, bounded thread pool rather than inheriting asyncio's
default executor (`min(32, cpu+4)`), otherwise the practical concurrency limit changes silently.

### 3.3 Multiple replicas, no shared bus
Deployed to GKE (`.github/workflows/release.yaml` dispatches `chatbot-deploy.yml` in
`Sefaria/AppliedAI-Infrastructure`). `CACHES` is unconfigured, so Django falls back to
per-process `LocMemCache` — already a correctness gap for the client-event rate limiter
(`views.py:215`), and a hard blocker for any push that has to reach a socket held by a different
pod. A reconnecting client can land on a different pod than the one running its turn.

### 3.4 Three non-browser consumers read the SSE endpoint
- `server/loadtest/load_test.py:116`
- `evals/run_eval.py:131`
- `latency/scripts/run_baseline_beta_from_braintrust.py:434`

Keeping SSE for these is both cheaper and better: they want a one-shot request/response, not a
session. **The endpoint stays; only the browser moves.**

### 3.5 CORS does not apply to WebSockets
`CORS_ALLOW_ALL_ORIGINS = True` (`settings.py:109`) is irrelevant to a WS handshake — the browser
sends `Origin` and the server must check it itself. This becomes a required, explicit allowlist.

### 3.6 Browsers cannot set headers on a WebSocket
Auth is an encrypted `userId` token in the POST body today. It cannot move to a header. It goes
in a first application-level frame, not a query string (query strings land in access logs).

### 3.7 The widget can be sending in more than one session at once
`LCChatbot.svelte` tracks `sendingSessionIds` as a map and checks `sessionId !== sendingSessionId`
in its callbacks. The protocol must multiplex — every frame carries its `sessionId` and
`messageId`.

---

## 4. Target architecture

```
Browser (one socket per open widget)
   │  wss://…/api/ws/chat        JSON frames, v1 envelope
   ▼
ASGI app (uvicorn worker under gunicorn)
   ├── http  → existing Django WSGI-equivalent stack (all current endpoints, incl. SSE)
   └── ws    → ChatConsumer ──► turn_stream.run_turn()  (transport-agnostic, blocking)
                                       └── agent thread + appetizer thread (unchanged)
```

### 4.1 Decision: Django Channels 4 + uvicorn

Recommended over a hand-rolled ASGI WebSocket handler:

- `AsyncJsonWebsocketConsumer` is ~the same amount of code we'd write by hand, but with the
  handshake, close codes, and framing already correct.
- `channels.testing.WebsocketCommunicator` gives real consumer tests with no live server —
  `pytest-asyncio` is already on `asyncio_mode = "auto"` (`pyproject.toml`), so tests drop in.
- `OriginValidator` and `database_sync_to_async` are provided, not reinvented.
- Phase 2 cross-pod push is a `channels-redis` config change plus a `group_send`, not a redesign.

Cost: two new deps (`channels>=4.0`, `uvicorn[standard]`) and one consumer module. No channel
layer is configured in Phase 1 — the consumer holds its own state.

**Rejected:** raw ASGI + `websockets`. Saves one dependency, costs us the test harness, the
origin validator, and the Phase 2 path.

### 4.2 Decision: extract a transport-agnostic turn runner

`generate_sse()` currently interleaves SSE formatting with the whole turn lifecycle (persistence,
summary, cost back-patching, heartbeats, Sentry). Duplicating that for WS would be a maintenance
trap, so it moves to `server/chat/V2/turn_stream.py`:

```python
def run_turn(*, data, actor, cancelled: threading.Event) -> Iterator[dict]:
    """Yields transport-neutral events:
       {"type": "progress", ...}  {"type": "message", ...}  {"type": "error", "error": str}
       Raises nothing to the caller; terminal state is always an event.
    """
```

The body is today's `generate_sse()` with three mechanical edits: `yield f"event: …"` becomes
`yield {...}`, the `stream_closed` nonlocal becomes the `cancelled` event, and the 4KB
proxy-flush comment and `": keepalive"` comment move to the SSE adapter (WS does not need
either). `chat_stream_v2` becomes a ~15-line adapter that formats those dicts as SSE; the
consumer sends them as JSON. **One code path, two transports** — which is also what makes the
rollout flag in §13 safe.

---

## 5. Wire protocol (v1)

Endpoint: `GET /api/ws/chat` → `wss://`. One socket per open widget; every frame is JSON.

**Envelope (both directions)**

```jsonc
{ "v": 1, "type": "<name>", "id": "<correlation id>", "data": { } }
```

`id` correlates a client request with all server frames for it; for a turn it is the client's
`messageId`, so the existing recovery endpoint keys still line up.

### 5.1 Client → server

| type | data | notes |
|---|---|---|
| `hello` | `{ userId, clientVersion, locale, origin, isStaff, labs }` | **Must be the first frame.** 5s timeout, else close `4401`. |
| `send` | the existing `ChatRequestSerializer` payload minus `userId` | `id` = `messageId`. |
| `cancel` | `{}` | `id` = `messageId` of the turn to abort. |
| `ping` | `{}` | Client-driven liveness, every 30s. |

### 5.2 Server → client

| type | data | notes |
|---|---|---|
| `ready` | `{ heartbeatTimeoutMs, serverVersion, maxConcurrentTurns }` | After a valid `hello`. |
| `progress` | today's progress payload verbatim | Same keys as the SSE `progress` event. |
| `message` | today's final payload verbatim (`_build_response_payload`) | Terminal for that `id`. |
| `error` | `{ error, code }` | Terminal for that `id`. |
| `pong` | `{}` | |
| `push` | `{ kind, payload }` | **The point of this change.** Server-initiated, `id` absent. |

Frames are byte-identical in their `data` to what SSE sends today, so `LCChatbot.svelte` needs no
changes to its handlers (§8).

### 5.3 Close codes

| Code | Meaning | Client behavior |
|---|---|---|
| `1000` | Normal | No reconnect |
| `1012` | Server restarting (SIGTERM drain) | Reconnect immediately with jitter |
| `4400` | Malformed frame / bad envelope | Reconnect once, then fail to HTTP fallback |
| `4401` | Auth missing, invalid, or expired | Do **not** reconnect; surface `userId_expired` as today |
| `4408` | Idle timeout | Reconnect lazily on next send |
| `4429` | Too many sockets or turns for this user | Backoff, then HTTP fallback |

---

## 6. Server-side changes

### 6.1 New files

| File | Contents |
|---|---|
| `server/chatbot_server/asgi.py` | `ProtocolTypeRouter`: `http` → `get_asgi_application()`, `websocket` → `OriginValidator(URLRouter(ws_urlpatterns), CHAT_WS_ALLOWED_ORIGINS)` |
| `server/chat/V2/turn_stream.py` | `run_turn()` — extracted from `generate_sse()` (§4.2) |
| `server/chat/V2/ws/consumer.py` | `ChatConsumer(AsyncJsonWebsocketConsumer)` |
| `server/chat/V2/ws/routing.py` | `path("api/ws/chat", ChatConsumer.as_asgi())` |
| `server/chat/tests/test_ws_consumer.py` | Consumer tests (§12) |

### 6.2 Consumer responsibilities

```
connect()          accept, start 5s auth timer, cap sockets per user_id
receive_json()     envelope validation → dispatch; reject frames >64KB
  hello            authenticate_request-equivalent on the token → Actor; send `ready`
  send             ChatRequestSerializer validation → acquire turn semaphore → start turn task
  cancel           set the turn's threading.Event
  ping             send `pong`
disconnect()       set cancelled on every in-flight turn, release semaphore slots
```

Turn execution, which is the one genuinely fiddly part:

```python
async def _run_turn(self, req_id, payload):
    loop = asyncio.get_running_loop()
    q = asyncio.Queue(maxsize=STREAM_PROGRESS_QUEUE_MAXSIZE)
    cancelled = threading.Event()

    def pump():                                    # runs on TURN_EXECUTOR
        for event in run_turn(data=payload, actor=self.actor, cancelled=cancelled):
            loop.call_soon_threadsafe(q.put_nowait, event)
        loop.call_soon_threadsafe(q.put_nowait, None)

    fut = loop.run_in_executor(TURN_EXECUTOR, pump)
    while (event := await q.get()) is not None:
        await self.send_json({"v": 1, "type": event.pop("type"), "id": req_id, "data": event})
    await fut
```

Notes that matter:
- `TURN_EXECUTOR` is an explicit module-level `ThreadPoolExecutor(max_workers=CHAT_MAX_CONCURRENT_TURNS)`,
  **not** the default executor. A `send` that cannot get a slot within ~1s gets
  `error {code: "busy"}` rather than queueing invisibly.
- Reuse of `ChatRequestSerializer`, `Actor`, and `run_turn` means the authorization and
  validation surface is identical to the HTTP path — no second implementation to keep in sync.
- The DB heartbeat writes (`_mark_turn_heartbeat` et al.) stay inside `run_turn` on the worker
  thread, so sync ORM is never touched from the event loop.

### 6.3 Settings

```python
ASGI_APPLICATION = "chatbot_server.asgi.application"
CHAT_WS_ALLOWED_ORIGINS   = env list, e.g. ["sefaria.org", "*.sefaria.org", "localhost:5173"]
CHAT_WS_MAX_SOCKETS_PER_USER = 3
CHAT_WS_IDLE_TIMEOUT_SECONDS = 600
CHAT_MAX_CONCURRENT_TURNS    = 8     # per pod; was implicitly 4 via --threads
```

`INSTALLED_APPS` gains `"channels"`. `requirements.txt` gains `channels>=4.0` and
`uvicorn[standard]>=0.30`.

### 6.4 Entrypoint

```dockerfile
ENTRYPOINT ["gunicorn", "chatbot_server.asgi:application", \
            "--bind", "0.0.0.0:8080", \
            "--worker-class", "uvicorn.workers.UvicornWorker", \
            "--workers", "2", "--timeout", "0", "--graceful-timeout", "90"]
```

`--timeout 0` is required: gunicorn's worker timeout kills workers holding long-lived sockets.
`--graceful-timeout 90` lets in-flight turns finish on SIGTERM; the consumer's `disconnect`
should close with `1012` so clients reconnect rather than error.

---

## 7. What does *not* change on the server

`chat_recover_v2`, `chat_client_event_v2`, the processing-state/heartbeat columns, the appetizer
thread, the agent, Braintrust wiring, `anthropic_views.py`, and all history endpoints. The
recovery machinery becomes *more* valuable with WS, not less (§9).

---

## 8. Client-side changes

### 8.1 New: `src/lib/socket.js`

A small connection manager, the only new concept on the client:

- `connect(apiBaseUrl, userId)` — derives the URL with `new URL(apiBaseUrl, location.href)` then
  swaps `http:`→`ws:` / `https:`→`wss:` (the widget is embedded with a *relative*
  `api-base-url="/api"`, per `index.html:193`, so absolute-URL construction is mandatory).
- Sends `hello`, resolves once `ready` arrives.
- `request(type, id, data, callbacks)` → returns a promise resolving on the terminal
  `message`/`error` frame for `id`, with `onProgress` fired per `progress` frame. Pending
  requests live in a `Map` keyed by `id` — this is what gives us §3.7 multiplexing for free.
- Reconnect: exponential backoff 1s → 30s with full jitter, capped; no reconnect on `4401`.
- Lifecycle: connect when the widget panel opens or on first send; close 60s after the panel
  closes; close on `pagehide`; reconnect on `visibilitychange` → visible if a turn is in flight.
- `onPush(handler)` — the hook the proactive-push work plugs into.

### 8.2 `src/lib/api.js`

`sendMessageStream()` keeps its exact signature and semantics and becomes:

1. Try the socket. On `message` → resolve, same shape as today.
2. On socket failure, terminal `error`, or a closed socket that will not reopen → fall back to
   the existing SSE path, unchanged.
3. Recovery (`recoverStreamMessage`) and telemetry (`reportClientStreamEvent`) are unchanged and
   now cover both transports; add `ws_connect_failed`, `ws_closed_midturn`, and
   `ws_fallback_to_sse` event names to the existing telemetry vocabulary so the rollout is
   measurable.

**`LCChatbot.svelte` is not modified.** That is the test that the abstraction is right.

---

## 9. Reconnect and recovery semantics

A dropped socket mid-turn is not a lost turn — the server side never depended on the connection:
`run_turn` persists the assistant message before emitting the terminal event, and the
`processing_state` / `processing_heartbeat_at` columns already describe in-flight work.

On reconnect with a turn in flight, the client:
1. Reconnects and re-sends `hello`.
2. Does **not** re-send `send` (that would duplicate the turn).
3. Polls `POST /v2/chat/recover` exactly as it does today, using the same `messageId`.

Server-side replay of missed frames is deliberately out of scope: with multiple replicas the
reconnect frequently lands on a different pod, so replay only works after the Phase 2 bus
exists. The HTTP recovery path already covers the case correctly and cheaply.

---

## 10. Infrastructure — the dependency outside this repo

These live in `Sefaria/AppliedAI-Infrastructure` and **must land before or with the rollout**:

1. **Backend timeout.** GCLB's default backend `timeoutSec` is 30s and it bounds *total
   WebSocket connection duration*. A `BackendConfig` with `timeoutSec: 3600` is required or every
   socket dies at 30s. This is the single most likely thing to break the launch.
2. **WebSocket upgrade** allowed on the ingress/proxy path (GCLB supports it on HTTP/1.1
   backends; confirm no intermediate proxy strips `Upgrade`).
3. **Readiness/liveness probes** — `/api/health` is unaffected, but confirm probes do not count
   long-lived connections against the pod.
4. **`terminationGracePeriodSeconds` ≥ 120** to match `--graceful-timeout 90`.
5. **HPA signal.** CPU alone is a poor proxy once connections outlive requests; add a
   connection-count or concurrent-turn metric.
6. **Session affinity** — *not* required by this design (§9), but worth enabling
   (`sessionAffinity: CLIENT_IP` or a cookie) if we later add server-side frame replay.

---

## 11. Proactive push (the reason for all of this)

Phase 1 gives a working push for anything originating **inside the pod holding the socket** —
e.g. the appetizer already arrives this way, and anything the turn pipeline wants to volunteer.
The `push` frame type and `onPush` handler exist from day one.

For a push originating anywhere else (a background job, another pod, a cron), add in Phase 2:

- `CHANNEL_LAYERS` → `channels_redis.core.RedisChannelLayer`.
- Consumer joins group `user.{user_id}` (and optionally `session.{session_id}`) on `hello`.
- A thin `publish_to_user(user_id, kind, payload)` helper wrapping `group_send`.

Nothing in the protocol or the client changes. Redis is worth introducing regardless — it also
fixes the per-process `LocMemCache` rate limiter (§3.3).

---

## 12. Testing

**New — `server/chat/tests/test_ws_consumer.py`** (`WebsocketCommunicator`, `pytest-asyncio` auto
mode already configured):
- rejects a `send` before `hello` (close `4400`)
- rejects an invalid/expired token (close `4401`, no turn started)
- happy path: `hello` → `ready` → `send` → ≥1 `progress` → terminal `message`, with the agent
  mocked exactly as `test_streaming_integration.py` does
- agent raises → terminal `error` frame **and** the error message row is persisted
- two concurrent `send`s in different sessions stay correlated by `id` (§3.7)
- `cancel` stops the turn and the DB row lands in a terminal processing state
- disconnect mid-turn still persists the assistant message (the §9 guarantee)
- turn semaphore exhausted → `error {code: "busy"}`, no thread leak

**Extended:**
- `test_streaming_integration.py` — keep every existing case; they now exercise the SSE adapter
  over the shared `run_turn`, which is exactly the regression net the refactor needs.
- New `server/chat/tests/test_turn_stream.py` for `run_turn` in isolation.

**Frontend:** `npm run build`, then manual verification against a local ASGI server —
happy path, mid-turn network drop → recovery, expired token, and forced `#test-stream-break`.

---

## 13. Rollout

Phases are independently shippable and each is a small, atomic commit series.

| Phase | Work | Risk |
|---|---|---|
| **1** | `refactor: extract transport-agnostic turn runner` — `turn_stream.py`, `chat_stream_v2` becomes an adapter. **No behavior change, no new deps.** | Low; covered by existing tests |
| **2** | `feat: serve the app over ASGI` — `asgi.py`, `channels`, `uvicorn`, Dockerfile entrypoint, infra timeouts. Still 100% HTTP/SSE traffic. | **Medium — this is the deployment risk, and it is worth shipping alone.** |
| **3** | `feat: websocket chat endpoint` — consumer, routing, origin validation, tests. Server-side only; nothing calls it yet. | Low |
| **4** | `feat: stream chat over websocket with SSE fallback` — `socket.js`, `api.js` shim, behind `ws-enabled` widget attribute defaulting off. | Low |
| **5** | Flip the default on after watching `ws_fallback_to_sse` telemetry on beta. | Low |
| **6** | Phase 2 push: Redis channel layer + `publish_to_user`. | Separate plan |

**Rollback.** Phase 4+ is an attribute flip — the SSE path stays live and tested. Phase 2 is the
one that needs a real image rollback, which is why it ships by itself.

**Rough size:** Phase 1 ≈ 250 lines moved. Phases 2–3 ≈ 300 new lines + tests. Phase 4 ≈ 200
lines. The infra change is small but on someone else's calendar — start it first.

---

## 14. Risks

| Risk | Mitigation |
|---|---|
| GCLB 30s backend timeout silently kills every socket | §10.1 — verify on beta before Phase 4 |
| ASGI migration changes request handling for all endpoints, not just chat | Phase 2 ships alone with full SSE traffic; existing test suite runs against it |
| Turn concurrency changes silently (4 threads → default executor) | Explicit `TURN_EXECUTOR` + `busy` error frame, sized in settings |
| Sync ORM called from the event loop → `SynchronousOnlyOperation` | All DB work stays inside `run_turn` on a worker thread; consumer only awaits the queue |
| Corporate proxies / old middleboxes block `Upgrade` | SSE fallback is permanent, not transitional; telemetry measures how often it fires |
| Socket count scales with open widgets across sefaria.org | Connect on panel open, idle-close at 10 min, per-user socket cap, HPA on connections |
| WS bypasses CORS | Mandatory `OriginValidator` allowlist (§3.5) |

---

## 15. Open questions

1. Does the GCLB path in front of `chat.sefaria.org` terminate at an ingress we control, or is
   there a CDN/WAF hop that would need WS allowances too?
2. Is introducing Redis acceptable now (it unblocks Phase 6 and fixes the rate limiter), or
   should Phase 1 push stay strictly in-pod?
3. What is the first real proactive-push use case? It decides whether the group key is
   `user.{id}` or `session.{id}`, and whether the socket must stay open while the panel is closed.
