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

---

## Context: we already run three hosts over one tool layer

The MCP consolidation (`b67f577`, `de89ee7`) established the pattern this design extends. Today:

| Host | Package | Deps | Tool set |
|------|---------|------|----------|
| Web chatbot | `server/chat/` (Django) | Django, anthropic, braintrust, Claude CLI | `get_tools_for_surface("agent")` |
| Public MCP | `server/mcp_server/` | fastmcp, httpx, uvicorn — **no Django** | `get_tools_for_surface("mcp")` |
| **Local runner** | `server/local_runner/` *(proposed)* | claude-agent-sdk, httpx, uvicorn | `get_tools_for_surface("agent")` |

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

**Implementation note (Phase 1).** Removing those three reads was necessary but not sufficient: the
loop still pulled Django *transitively*, because `prompts/__init__.py` eagerly imported
`prompt_service`, and the guardrail and router wrappers imported their Django-backed services at
module scope. Phase 1 therefore also made the prompts package lazy (PEP 562, matching the agent
package) and moved those two service lookups into the calls that use them. The classes stay
importable, so a host can inject its own `GuardrailGate` and prompt service — which is exactly what
the runner does, since both the guardrail and the prompt bundle come from our server.

`chat/tests/test_agent_django_independence.py` locks this in: it blocks `django` on `sys.meta_path`
in a subprocess and imports every module a non-Django host needs.

Critically for observability: **`trace_logger.py` and `metrics_mapper.py` are also clean.** They
build Braintrust payloads against an opaque `bt_span: Any` and import no Braintrust library. This is
what makes trace parity achievable — see *Observability* below.

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

A small ASGI app, modeled on `mcp_server/app.py`:

```
server/local_runner/
├── __init__.py
├── __main__.py       # uvicorn.run(build_app(), host="127.0.0.1", port=...)
├── app.py            # routes: /health, /pair, /chat/stream
├── pairing.py        # code generation, token mint + verify
├── bundle.py         # fetch prompt bundle + guardrail verdict from our server
└── trace.py          # buffer spans, POST assembled traces to our server
server/requirements-local.txt
```

Its job: accept a POST matching the current `/chat/stream` request schema, fetch the bundle and
guardrail verdict, run the agent loop via the shared core, stream SSE events in the existing format,
and post the assembled trace back.

**The SSE contract is unchanged** — `event: progress`, `event: message`, `event: cancelled`,
`event: error` (`chat/V2/views.py:620,836,663,693`). Because the progress events are byte-identical,
the progress trail and tool cards render with no frontend work.

### Component 3 — server additions

| Endpoint | Purpose |
|----------|---------|
| `POST /api/v2/local/pair` | Exchange a pairing code for a runner token bound to the user token |
| `GET /api/v2/local/bundle` | Prompt bundle, model id, `AgentConfig` values, version verdict |
| `POST /api/v2/local/guardrail` | Guardrail verdict for one message |
| `POST /api/v2/local/trace` | Assembled trace intake, replayed into Braintrust server-side |

`local/bundle` is also the **version gate and kill switch**: it can refuse an unsupported runner or
CLI version, and can return `mode: "server"` to force fallback if we need to disable local mode
globally.

### Component 4 — frontend

Minimal. `LCChatbot.svelte:31` already accepts `api-base-url` as an attribute. Local mode is:

1. On load, `GET http://127.0.0.1:8899/health` with a short timeout.
2. On success and a valid paired token, set the effective base URL to the runner.
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
| **Localhost HTTP daemon** | One CLI package | **Chosen.** Speaks our existing SSE contract; debuggable with curl; no browser-store review in the release path. |
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
| 1 | `AgentConfig` + call-time resolution of Django-backed services | **Done** (`3d9c07e`, `91ca04c`). Production, no behavior change. |
| 2 | `server/local_runner/` + pairing + bundle + guardrail proxy + trace intake + frontend health check | Local mode at parity, closed beta, behind a flag. |
| 3 | Extension as detector and PNA fallback | Resilience against a browser policy change. |
| 4 | `"local"` surface: runner-owned structured store (SQLite), typed collection tools | The capability we cannot offer server-side. |

Phase 1 is independently valuable and independently reviewable. Do not bundle it into Phase 2.

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
