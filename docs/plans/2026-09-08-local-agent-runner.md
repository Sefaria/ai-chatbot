# Local Agent Runner — Bring Your Own Claude

**Status:** Proposed
**Date:** 2026-09-08
**Author:** Akiva Berger

**Goal:** Let a sefaria.org user run the chatbot's agent loop on their own machine against their own
Claude subscription, with **behavioral parity** to the hosted agent and **full Braintrust logging**,
so that later phases can hand the local agent capabilities the hosted one cannot have.

**Non-goals:** Replacing the hosted experience; offline use; **filesystem or shell tools, ever**;
supporting browsers other than Chrome-family for the fallback transport.

---

## Motivation

Every agent turn today runs on our server and bills Claude tokens to our `ANTHROPIC_API_KEY`.
Phase 0 (the public MCP at `mcp.sefaria.org`) confirmed real user appetite for pointing their own
Claude at Sefaria — but it happens in *their* client, outside our UI, with none of our prompts,
guardrail, or progress trail.

Local mode closes that gap: our interface, our prompts, our tools, their subscription.

The priorities, in order:

1. **Parity.** The local agent behaves like the hosted agent. Same tools, same prompts, same
   guardrail, same progress trail.
2. **Observability.** Conversations log to Braintrust exactly as hosted ones do.
3. **Headroom.** Local execution unlocks capabilities we cannot offer server-side, handed over
   deliberately in later phases.

Two things worth stating plainly, because they shape the design:

- **The cost we shift is Claude tokens, not compute.** The Sefaria tools are `httpx` calls to
  sefaria.org (`sefaria_client.py`), which are cheap. Framing this as "agentic search is expensive on
  our server" overstates the infra savings and understates the token savings.
- **We gain a load pattern we do not currently have.** Agentic search from N local machines hits the
  Sefaria API directly, bypassing the implicit throttle of passing through our own server. API-side
  rate limits keyed to the user token are a prerequisite, not a follow-up.

---

## Decisions

### D1 — Terms of service: proceeding

**Decision (Akiva, 2026-09-09): not a blocker. Proceed.**

The rationale: we are providing an *interface* through which a user interacts with their own Claude
subscription. The user installs the runner, authenticates their own `claude` CLI, and our chat UI
talks to a daemon on their machine. The subscription relationship stays between the user and
Anthropic; we supply prompts and tools, not credentials or quota.

Recorded here as a decision rather than an open question so it is not relitigated. If Anthropic
guidance later contradicts it, the fallback is a user-supplied `ANTHROPIC_API_KEY` in place of
subscription auth — the same runner architecture, a weaker pitch.

### D2 — No filesystem or shell tools

**Decision (Akiva, 2026-09-09): dropped permanently, not deferred.**

Local tooling headroom is served instead by a **runner-owned structured store** (SQLite): the agent
accumulates and traverses data across a session without any general file or shell access.

One implementation invariant matters more than the framing. SQLite is itself a file, so the safety
property is not "does not touch the filesystem" — it is:

> The runner owns the database file and its location. Tools expose **typed operations over named
> collections**. The agent never receives a path, a connection string, or raw SQL.

If a `db_path` parameter or a `query(sql)` tool ever appears, the property is gone and we are back to
arbitrary file access with extra steps.

### D3 — The daemon runs Django, not a separate framework

**Decision (Akiva, 2026-09-09): Django + SQLite.**

"No Django" was the wrong constraint. The V2 path reads 17 Django settings and every one is
non-secret configuration — prompt slugs and model names. What genuinely cannot ship to a user's
machine is much shorter: `BRAINTRUST_API_KEY`, `CHATBOT_USER_TOKEN_SECRET`, and the Postgres
connection.

Running Django in the daemon buys the thing parity most depends on: `chat_stream_v2` is ~900 lines of
SSE framing, cancellation, recovery and `processing_state` bookkeeping, and reimplementing a subset
of it is where drift would actually appear. The daemon reuses the view itself.

That view is heavily DB-coupled (`ChatMessage.objects` throughout), which is what SQLite is for.
Turn logging, summarization and history are all plain ORM and run on SQLite unchanged.

### D4 — Local history is authoritative; Braintrust is the record

**Decision (Akiva, 2026-09-09): the server does not record local-mode conversations.**

Because the daemon serves the same Django URLs and the widget points `api-base-url` at it,
`/api/history`, `/api/v2/chat/recover`, `/api/v2/chat/feedback`, summarization and turn logging all
work locally with **no new code**.

Making the server canonical instead would cost a turn-ingest endpoint, idempotency on `messageId`, a
retry queue, server-side session creation, and reconciliation when a user toggles local mode
mid-session. The blocker is summarization: it reads session history to build the next prompt, so a
canonical server either puts a write-then-fetch round trip on every turn's critical path, or forces
double-writes that diverge.

**Accepted losses:** cross-device continuity for local-mode conversations; Postgres product
analytics for those users; history visibly changing when local mode is toggled.

**Retained:** Braintrust traces carry full conversation content, metrics and `origin:
"sefaria-local"` — the record that matters for evals and quality analysis.

> [!warning] Dashboards will undercount
> Any Postgres-based session or message metric silently undercounts once local mode has volume. Wire
> the `sefaria-local` origin tag up **before** the beta so the gap is measurable.

**Optional add-on, not Phase 2.** A fire-and-forget turn POST (server writes it if it arrives, drops
it if not) recovers most analytics cheaply — but only while nothing depends on it. If it ever becomes
authoritative, every cost above comes back.

---

## Context: we already run three hosts over one tool layer

The MCP consolidation (`b67f577`, `de89ee7`) established the pattern this design extends. Today:

| Host | Package | Deps | Tool set |
|------|---------|------|----------|
| Web chatbot | `server/chat/` (Django) | Django, anthropic, braintrust, Claude CLI | `get_tools_for_surface("agent")` |
| Public MCP | `server/mcp_server/` | fastmcp, httpx, uvicorn — **no Django** | `get_tools_for_surface("mcp")` |
| **Local runner** | `server/local_runner/` *(proposed)* | Django, claude-agent-sdk, httpx, uvicorn, SQLite | `get_tools_for_surface("agent")` |

`server/requirements-mcp.txt` already documents the invariant we rely on:

> Deliberately excludes Django, anthropic, braintrust and the Claude Code CLI: the shared tool layer
> under `chat/V2/agent` depends only on httpx and stdlib.

So the local runner is not a new architecture. It is a third host built the way the second one was:
its own package, its own requirements file, its own `__main__.py`, importing the shared tool layer
and selecting a surface.

### What this means for scope

An earlier sketch proposed extracting a `sefaria-agent-core` package as a prerequisite. **That
extraction is largely already done** — `mcp_server` proves the tool half (`tool_schemas`,
`tool_executor`, `sefaria_client`) runs outside Django today.

What remains is the *agent-loop* half, and the coupling is three settings reads:

| Location | Setting |
|----------|---------|
| `chat/V2/agent/claude_service.py:84` | `BRAINTRUST_LOGGING_ENABLED` |
| `chat/V2/agent/claude_service.py:92` | `AGENT_MODEL` / `LOAD_TEST_MODEL` |
| `chat/V2/agent/turn_orchestrator.py:140` | `RESPONSE_FORMAT_PROMPT_SLUG` |

`prompt_fragments.py` is free of both Django and Braintrust; only `prompt_service.py` binds to
Braintrust. `sdk_runner.py`, `sdk_options_builder.py` and `tool_runtime.py` are already clean.

**Implementation note (Phase 1).** `AgentConfig` shipped in `3d9c07e`. A follow-up (`91ca04c`) also
made the prompts package lazy and moved the guardrail and router service lookups to call time, to get
the agent loop importing with no Django at all — that was **reverted in `b017b4a`** when D3 settled
that the daemon runs Django. `AgentConfig` stands on its own: it turns three hidden global settings
reads into an explicit contract the daemon fills from the bundle.

The tool layer's Django independence is a separate, still-live constraint (`requirements-mcp.txt`),
now covered by `chat/tests/test_tool_layer_independence.py`.

---

## Design

### What moves, what stays

A turn runs six stages. Exactly one relocates.

```
STAYS ON OUR SERVER
  Guardrail check  →  Prompt assembly  →  Route  →  [ ]  →  Persist + trace  →  Stream to UI
     (brand safety)     (Braintrust)      (router)   ↑        (Postgres)
                                                     │
MOVES TO THE USER'S MACHINE                          │
                              Agent loop  ───────────┘
                        (claude CLI + Sefaria tools)
```

The guardrail, the prompts and the Braintrust write are the controls we do not want a user able to
edit. They stay server-side. The runner receives a prompt bundle per session; it never holds
`BRAINTRUST_API_KEY`.

We are not sending messages **to** a terminal. The browser talks directly to a local process that
runs the same agent code the server runs, and the server stays in the loop around it.

### Component 1 — `AgentConfig` (shared core) — **done**

A frozen dataclass in `chat/V2/agent/contracts.py` carrying the three values above.
`ClaudeAgentService` and `TurnOrchestrator` take it as a constructor argument instead of reading
`django.conf.settings`. The Django host builds it from settings; the runner builds it from the
bundle.

This is the only change to existing production code paths, and it ships independently with no
behavior change.

### Component 2 — `server/local_runner/`

A Django settings profile and entrypoint, not a second web framework:

```
server/local_runner/
├── __init__.py
├── __main__.py       # migrate, then serve 127.0.0.1 (uvicorn over the ASGI app)
├── settings.py       # local-mode Django settings: SQLite, no Braintrust key, no Postgres
├── pairing.py        # code generation, token mint + verify
├── bundle.py         # fetch prompt bundle + config from our server
├── prompt_source.py  # PromptService implementation backed by the bundle, not Braintrust
├── gates.py          # GuardrailGate + Router implementations that call our server
└── trace.py          # buffering span; POST assembled traces to our server
server/requirements-local.txt
```

`chat_stream_v2` and the rest of the URL surface are reused as-is. What the daemon replaces is only
what needs our secrets:

| Server-side dependency | Local replacement |
|---|---|
| `PromptService` (Braintrust) | `prompt_source.py`, fed by `GET /api/v2/local/bundle` |
| Guardrail service (Braintrust model call) | `POST /api/v2/local/guardrail` on our server |
| Router service (Braintrust model call) | `POST /api/v2/local/route` on our server |
| Braintrust span writes | buffered locally, replayed server-side from `POST .../trace` |
| Postgres | local SQLite (D4) |

Because these are injected rather than forked, the SSE contract, cancellation semantics and recovery
path are the production ones by construction rather than by careful reimplementation.

### Component 3 — server additions

| Endpoint | Purpose |
|----------|---------|
| `POST /api/v2/local/pair` | Exchange a pairing code for a runner token bound to the user token |
| `GET /api/v2/local/bundle` | Prompt bundle, model id, `AgentConfig` values, version verdict |
| `POST /api/v2/local/guardrail` | Guardrail verdict for one message |
| `POST /api/v2/local/route` | Router classification for one message |
| `POST /api/v2/local/trace` | Assembled trace intake, replayed into Braintrust server-side |

No turn-ingest endpoint: local conversations are not recorded in Postgres (D4).

`local/bundle` is also the **version gate and kill switch**: it can refuse an unsupported runner or
CLI version, and can return `mode: "server"` to force fallback if we need to disable local mode
globally.

### Component 4 — frontend

Minimal. `LCChatbot.svelte:31` already accepts `api-base-url` as an attribute. Local mode is:

1. On load, `GET http://127.0.0.1:8899/health` with a short timeout.
2. On success and a valid paired token, set the effective base URL to the runner — which also
   redirects history, recovery and feedback to the local store, since the daemon serves those URLs.
3. On any failure at any point — daemon down, crash, different device — revert to our server
   **silently**. Local mode is an optimization, never a dependency.

### Surface model

The runner uses `get_tools_for_surface("agent")`, not a new surface. Parity is the point; a separate
surface would fragment behavior for no benefit.

A `"local"` surface is the home for the structured-store tools in Phase 4, at which point
`local_runner` selects the union of `"agent"` and `"local"`.

---

## Parity

Parity is the top requirement, so the threats to it are called out explicitly. All three are real and
all three are manageable.

### P1 — Model availability

The bundle returns a model id, but a subscription user's available models may not match
`AGENT_MODEL`. If the model differs, behavior differs, and parity is nominal rather than actual. The
runner must report the model actually used in its trace, and the bundle must be able to refuse rather
than silently substitute.

### P2 — CLI version drift *(the subtle one)*

`SDKOptionsBuilder._supports_option` (`sdk_options_builder.py:39-49`) does **runtime feature
detection** and silently omits options the installed SDK does not accept — `max_tokens`,
`temperature`, `thinking`, `include_partial_messages`.

On our server we pin that version. On a user's machine we do not. An older local CLI will quietly
drop `temperature` and `thinking: disabled` and produce visibly different output with no error
anywhere.

**Mitigation:** the runner reports its `claude-agent-sdk` and CLI versions to `local/bundle`; the
server refuses versions below a floor. The runner also logs which options were dropped into the
trace, so drift is visible in Braintrust rather than invisible in the field.

### P3 — Guardrail latency

The guardrail stays server-side, so each turn adds a round trip local mode does not otherwise need.
The bundle is cacheable per session; the guardrail call is not. Acceptable, but it means local mode
is not automatically faster — it removes our agent queue and adds one hop.

---

## Observability

Braintrust logging is a hard requirement, not telemetry garnish. The design works because the payload
builders are already decoupled from the Braintrust client.

**Mechanism.** `BraintrustTraceLogger` and `build_braintrust_metrics` take an opaque span object and
build payloads. The runner passes a **buffering span** that records `log()` calls instead of writing
them, then posts the buffer to `POST /api/v2/local/trace`. Our server replays it into Braintrust
under our key. Same payload shape, same metrics, no key on the client.

Token counts survive: `ResultMessage` carries usage, and `metrics_mapper.map_usage` is a pure
function over that dict.

**Origin tagging — use the existing mechanism.** `chat/V2/origin.py` already resolves a caller origin
into `metadata.origin`, with `PROD_ORIGINS = {"sefaria-production"}` controlling the `"dev"` tag.
Local mode sends `origin: "sefaria-local"`. **Decision needed:** whether `"sefaria-local"` joins
`PROD_ORIGINS`. Real users on local mode are not dev traffic, so it probably should — but that
silently changes what existing Braintrust dashboards count as production.

**The one genuine gap.** `setup_claude_agent_sdk` (`claude_service.py:147`) patches the SDK in-process
to auto-emit fine-grained LLM-call spans, and it needs the Braintrust key in that process. We cannot
ship that key. So local traces will carry our turn-level spans and metrics, but not the SDK's
automatic sub-spans, unless the runner reimplements that capture. **Accept coarser sub-span detail in
Phase 2**; revisit only if the gap actually impairs evals.

---

## Transport

**Decision: localhost HTTP daemon.** The page fetches `http://127.0.0.1:8899` directly.

| Option | User installs | Verdict |
|--------|---------------|---------|
| **Localhost HTTP daemon** | One CLI package | **Chosen.** Serves our existing Django URL surface, so the SSE contract is the production one; debuggable with curl; no browser-store review in the release path. |
| Extension + native messaging | Extension + native host binary + OS-specific manifest | Rejected for v1. Three-part install, per-browser, Web Store review latency on every change. |
| Extension alone | Extension | Not viable. An extension cannot spawn the `claude` CLI. |
| Outbound WebSocket relay | One CLI package | Deferred. The fallback if PNA tightens, and the only option if browser and agent are on different machines. |

### Private Network Access

Chrome sends a preflight carrying `Access-Control-Request-Private-Network: true` for
https→localhost; the runner must answer `Access-Control-Allow-Private-Network: true`. Well-trodden
(Figma's font helper, Ledger Live), but it is the one browser behavior that could change under us —
hence the WebSocket relay staying on the shelf as a designed fallback.

### What the existing `chrome-extension/` is for

Two jobs, neither of them transport: **detection** (telling the page a runner is installed before it
attempts a fetch that may be blocked) and **escape hatch** (an extension reaches localhost without
the PNA preflight, so if Chrome tightens the rule the extension becomes the relay and nothing else
changes). Phase 3.

---

## Security model

Dropping filesystem and shell tools (D2) narrows the threat model substantially. The runner spawns
Claude with `permission_mode: "bypassPermissions"` (`sdk_options_builder.py:62`), but the only tools
reachable are the Sefaria HTTP tools and, later, the runner-owned store. The realistic harm from an
unauthenticated local request is **quota burn and conversation exfiltration**, not host compromise.

That is a much smaller blast radius. The controls are still required, because quota burn and reading
someone's conversation history are real harms:

- **Bind loopback only.** `127.0.0.1`, never `0.0.0.0`. Note `mcp_server/__main__.py` binds `0.0.0.0`
  — correct for a container, wrong here. Do not copy that line.
- **Bearer token on every request**, minted at pairing. This is the actual control.
- **`Origin` allowlist**, validated server-side against sefaria.org hosts. CORS governs *reading*
  responses, not *sending* requests, so it is not sufficient alone.
- **`Host` header validation** to defeat DNS rebinding.
- **Store scoping.** Phase 4 tools operate on named collections within the runner-owned database. No
  paths, no raw SQL (D2).
- **Trim the local URL surface.** The daemon runs Django, so it must expose only the routes local
  mode needs. Admin, and any management or debug endpoint, stay out of `local_runner/settings.py`;
  `DEBUG` is never on.

---

## Distribution

Ship as a `uv` tool for the beta:

```bash
uvx sefaria-agent start
```

The user must already have `claude` installed and authenticated — that is the real gate, and it means
the audience has already cleared a developer-tool install bar. A signed menubar app with auto-update
is weeks of code-signing and notarization work; earn it with beta adoption numbers first.

---

## Phasing

| Phase | Scope | Ships |
|-------|-------|-------|
| 0 | Public MCP at `mcp.sefaria.org` | **Done.** Demand confirmed. |
| 1 | `AgentConfig` — settings passed in rather than read from `django.conf` | **Done** (`3d9c07e`). Production, no behavior change. |
| 2 | `server/local_runner/` Django profile + SQLite + pairing + prompt/guardrail/route/summary proxies + frontend detection | **Built** (`20c9208`, `215a222`, `38b5d62`). Turn path verified end to end. |
| 2.5 | Braintrust trace replay | Deferred by decision (D5). |
| 3 | Extension as detector and PNA fallback | Resilience against a browser policy change. |
| 3.5 | *Optional:* fire-and-forget turn POST for analytics (D4) | Only if Postgres undercounting starts to hurt. |
| 4 | `"local"` surface: runner-owned structured store (SQLite), typed collection tools | The capability we cannot offer server-side. |

Phase 1 is independently valuable and independently reviewable. Do not bundle it into Phase 2.

---

### D5 — Observability deferred

**Decision (Akiva, 2026-09-09): ship local mode without Braintrust traces.**

Local turns are not logged. Everything else about them matches the hosted agent,
including summaries, which are proxied rather than dropped: the agent receives
only the current message, so the summary is the whole multi-turn memory and
losing it would quietly make local mode single-turn.

The topic appetizer stays disabled — it makes its own Anthropic call, and the UI
already treats it as optional.

**Consequence:** local-mode turns appear in no dashboard and in no eval. Until
trace replay lands, the only measure of local usage is the pairing endpoint.

---

## Open questions

1. **Sefaria API rate limiting.** Local agents hit sefaria.org directly. Limits keyed to the user
   token need to exist before local mode has volume. Owner: needs assignment.
2. **`PROD_ORIGINS` membership.** Does `"sefaria-local"` count as production for Braintrust tagging?
   Affects existing dashboards.
3. **Model pinning vs negotiation** (P1). Does the bundle pin a model and refuse mismatches, or
   negotiate one with the runner and record what was used?
4. **CLI version floor** (P2). What minimum `claude-agent-sdk` / CLI version does the bundle require,
   and how loudly does the runner fail below it?
5. **Port collision.** `8899` is a placeholder. Fixed port (simple discovery, collides) or a port file
   in a known location (robust, one more thing to find)?

---

## References

- `server/mcp_server/app.py` — the second-host pattern this design follows
- `server/requirements-mcp.txt` — the dependency-isolation invariant
- `server/chat/V2/origin.py` — origin resolution and `PROD_ORIGINS`
- `server/chat/V2/agent/trace_logger.py`, `metrics_mapper.py` — Braintrust-client-free payload builders
- `server/chat/V2/agent/sdk_options_builder.py:39-49` — runtime option detection (parity threat P2)
- `server/chat/V2/views.py:620,836` — the SSE contract the runner must match
- `docs/archive/2026-02-26-braintrust-origin-tagging.md` — prior art for trace origin tagging
- `chrome-extension/` — existing injector, repurposed in Phase 3
