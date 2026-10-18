# MCP Consolidation: serve the public Sefaria MCP from ai-chatbot, then add user auth

**Date:** 2026-10-18
**Status:** Proposed
**Branch:** `feat/mcp-consolidation` (off `main`)
**Supersedes:** `docs/superpowers/specs/2026-09-03-mcp-consolidation-design.md` on the
unmerged `local-agent-thin` branch. Phase 1 reuses most of that work. Where this plan
differs from it, the reason is given.

## Goal

1. **Phase 1, migration.** `mcp.sefaria.org` and `devmcp.sefaria.org` are served from
   this repo, by the same `SefariaToolExecutor` the Library Assistant uses. After that,
   `Sefaria/sefaria-mcp` can be archived. No authentication yet, and existing connector
   URLs keep working.
2. **Phase 2, auth.** Users can sign in to the MCP with their Sefaria account (OAuth,
   per the MCP authorization spec). The server then holds a Sefaria token for that user
   and exposes the source-sheet tools.

## Where things stand

| | Today |
|---|---|
| Public MCP | `Sefaria/sefaria-mcp`, a FastMCP app with a single replica on the AI cluster. HelmRelease `mcp` lives in `AppliedAI-Infrastructure/sefaria-ai/cluster-1/{dev,prod}/mcp.yaml`, with Service `sefaria-mcp` on port 8088 and metrics on 9090. Hosts: `devmcp.sefaria.org` / `mcp.sefaria.org`. |
| Its transports | Upstream `main` serves `/mcp` (Streamable HTTP, `stateless_http=True`, Host/Origin allowlist) **and** `/sse`, plus `/healthz` and the empty OAuth well-known stubs. |
| Its tools | 14 tools: `get_text`, `text_search`, `get_current_calendar`, `get_links_between_texts`, `search_in_book`, `search_in_dictionaries`, `get_english_translations`, `get_topic_details`, `clarify_name_argument`, `clarify_search_path_filter`, `get_text_or_category_shape`, `get_text_catalogue_info`, `get_available_manuscripts`, `get_manuscript_image`. Prod is older and still exposes the broken `english_semantic_search`. |
| Chatbot | Django, WSGI (`gunicorn gthread`), 3 replicas in prod. HelmRelease `chatbot` with Service `chatbot` on 8080 and hosts `chat-dev` / `chat.sefaria.org`. No ServiceMonitor. |
| Chatbot tools | `chat/V2/agent/tool_schemas.py`, `tool_executor.py`, `sefaria_client.py` (httpx, Django-free). Source-sheet tools (`search_user_source_sheets`, `get_source_sheet`, `create_source_sheet`) are Labs-only. They authenticate to Sefaria by sending the user's encrypted chatbot token as `X-Session-ID`. |
| Prior work | `local-agent-thin` has `de89ee76` (`surfaces` marker), `b67f577b` (`server/mcp_server/`, `Dockerfile.mcp`, release job, tests) and `105cdcc8` (`validate_response_links`, `validate_refs` on MCP). None of it is on `main`. |

## Decisions

| # | Decision | Choice | Why |
|---|---|---|---|
| D1 | Hosting | **One repo and one codebase, deployed as its own Deployment.** The existing `mcp` HelmRelease is repointed at an image built from this repo. | This keeps public MCP traffic off the chatbot's capacity and failure path (the chatbot ships to every Sefaria user), and the chatbot stays WSGI. The "route on the chatbot server" is an HTTPRoute (below), not shared pods. |
| D2 | Routes | Keep `mcp.sefaria.org` and `devmcp.sefaria.org` routed to the MCP Deployment. Optionally also route `chat[-dev].sefaria.org/mcp` to the same Service. | Existing connectors must not have to reconfigure. The chat-host route is a cheap alias if we want one canonical Sefaria AI host. |
| D3 | Image | **Same image as the chatbot, different command** (`python -m mcp_server`), replacing b67f577b's separate `Dockerfile.mcp`. | Phase 2 needs Django settings and secrets in the MCP process (D9). A second slim image saves memory but doubles release plumbing. If image size becomes a problem, revisit. |
| D4 | Tool selection | The `surfaces` marker on each schema (from `de89ee76`). Agent-only is the default. | This extends the `LABS_TOOL_NAMES` pattern, and forgetting the marker can't leak a tool. |
| D5 | Tool names | LA names, no aliases (`text_search` becomes `specific_keyword_search`). | Kept from the September decision. Connector setup is the contract, and tool names are not, because the model adapts to whatever names it sees. |
| D6 | OAuth well-known (Phase 1) | **Keep the empty 200 stubs.** This reverses b67f577b. | b67f577b removed them on the premise that stubs break claude.ai. That premise did not reproduce: on 2026-08-17 the 404 build broke the claude.ai settings **Connect** button and the stubs connected cleanly (sefaria-mcp PRs #25 → #28; wiki `repos/sefaria-mcp/oauth-discovery`). Phase 1 aims for parity, so the stubs stay. Phase 2 replaces them on the authed endpoint with populated documents. |
| D7 | Transport state | `/mcp` stateless (`stateless_http=True`) plus `/sse` for legacy clients. Host/Origin protection is ported from upstream `main`. | Stateless `/mcp` survives rollouts and can scale out. `/sse` keeps session state in pod memory, so the Deployment **stays at 1 replica** until SSE traffic is gone, or until we add consistent-hash on `session_id` for `/messages`. |
| D8 | Auth topology | **Two endpoints.** `/mcp` and `/sse` stay public and authless. **`/mcp/user`** requires OAuth and serves the public tools plus the sheet tools. | MCP clients only start OAuth when they get a 401, so optional auth on one endpoint would never prompt a login. A separate authenticated endpoint keeps anonymous use frictionless. |
| D9 | Authorization server | **The Django chatbot app is the OAuth authorization server.** It gets Sefaria identity through a login handoff on sefaria.org. The MCP process is a resource server that validates the access tokens. | Sefaria is not an OAuth server: it is only an allauth client of Google and Apple, and it has no authorize, token or well-known endpoints. Our Postgres already holds users and can store clients, codes and tokens. Building an AS into Sefaria-Project is a much larger change. |
| D10 | Sefaria credential | Sefaria mints a **new `aud: "mcp"` token** for the user, scoped to the sheet APIs and with a longer TTL. Django stores it encrypted, and MCP sends it as `X-Session-ID`. The chatbot never mints Sefaria tokens itself. | This mirrors the existing admin MCP token (`AdminMCPTokenView`, `SessionIDAuthMiddleware` audience check). If the chatbot minted tokens from the shared secret, it would hold impersonate-anyone power for long-lived sessions. |

## Phase 1: migrate the public MCP (no auth)

Done means: `mcp.sefaria.org/sse` and `/mcp` are served by this repo's image, tool calls
succeed, metrics are scraped, and `Sefaria/sefaria-mcp` is archived.

### 1.1 Bring the prior work onto `main`

- [ ] Cherry-pick `de89ee76`, `b67f577b` and `105cdcc8` from `local-agent-thin` and
      resolve conflicts against current `tool_schemas.py`. `main` has the same 21 tools.
- [ ] Make `chat/V2/agent/__init__.py` lazy, if that isn't already part of the
      cherry-picks, so importing the tool layer doesn't pull in `claude_agent_sdk`,
      `anthropic` or `braintrust`.
- [ ] `server/chat/tests/test_mcp_server.py` passes under `pytest`.

### 1.2 Bring the server to upstream parity

These come from sefaria-mcp `main`. b67f577b predates them.

- [ ] `stateless_http=True` on `/mcp`.
- [ ] Host/Origin protection with `SEFARIA_MCP_ALLOWED_HOSTS` / `SEFARIA_MCP_ALLOWED_ORIGINS`.
      The defaults are both public hosts, plus `chat[-dev].sefaria.org` if D2's alias is
      used. Also add the `SEFARIA_MCP_HOST_PROTECTION=off` escape hatch. Empty lists fall
      back to defaults: an empty allowlist makes `/mcp` loopback-only.
- [ ] Restore the four well-known stubs (D6), with `resource` set per environment from
      `SEFARIA_MCP_PUBLIC_URL`, not hardcoded to devmcp. Prod currently advertises the
      dev host.
- [ ] `/healthz`.
- [ ] Port the transport tests from sefaria-mcp `tests/test_transports.py`: routing,
      the Host/Origin guard, and `/sse` not redirecting to `/sse/`.
- [ ] Sefaria User-Agent on outbound requests. Check whether `main` already has it
      (PR #213, `feat/sefaria-user-agent`).
- [ ] Pin `fastmcp` to the 4.x line upstream already uses (`>=4.0.3,<5`). b67f577b pinned
      `3.4.7`.

### 1.3 Public tool list

The `mcp` surface (19 tools, plus 2 for review):

| Tool | Notes |
|---|---|
| `get_text` | |
| `specific_keyword_search` | was `text_search` |
| `semantic_search` | calls `www.sefaria.org/api/knn-search` and needs `SEMANTIC_SEARCH_API_TOKEN`. **Decision needed:** whether to spend that token on anonymous traffic. If not, leave the tool off the surface. |
| `get_current_calendar` | |
| `get_links_between_texts` | |
| `search_in_book`, `search_in_dictionaries` | |
| `get_english_translations` | |
| `get_topic_details` | |
| `clarify_name_argument`, `clarify_search_path_filter` | |
| `catalog_get_node`, `catalog_get_children`, `catalog_search`, `catalog_query` | new on the public surface |
| `get_text_or_category_shape`, `get_text_catalogue_info` | `mcp`-only legacy schemas, kept for parity. Retire later in favour of `catalog_*`. |
| `get_available_manuscripts`, `get_manuscript_image` | |
| `validate_refs`, `validate_response_links` | added by `105cdcc8`. Confirm we want them public. |

Not public: `search_user_source_sheets`, `get_source_sheet`, `create_source_sheet`.
They go on `/mcp/user` in Phase 2.

### 1.4 Packaging and release

- [ ] Drop `Dockerfile.mcp` and the separate `build_mcp` job (D3). The chatbot image
      gains `fastmcp`, `uvicorn` and `prometheus-client` in `requirements.txt`.
- [ ] `python -m mcp_server` runs uvicorn on `SEFARIA_MCP_PORT` (8088) and exposes
      metrics on `SEFARIA_MCP_METRICS_PORT` (9090). Metric names stay the same as today:
      `mcp_tool_calls_total`, `mcp_tool_duration_seconds`, `mcp_tool_payload_bytes`,
      `mcp_errors_total`.
- [ ] `SEFARIA_MCP_VERSION` is set from the release tag, so `serverInfo.version` reports
      our release.

### 1.5 Infrastructure (AppliedAI-Infrastructure)

- [ ] `sefaria-ai/cluster-1/{dev,prod}/mcp.yaml`: change the image to
      `us-docker.pkg.dev/sefaria-ai/container/ai-chatbot-server`, add command
      `python -m mcp_server`, and keep ports 8088/9090, the ServiceMonitor, and
      `replicas: 1` (D7).
- [ ] `mcp-config.yaml` / `mcp-secret.sops.yaml`: add `SEFARIA_MCP_PUBLIC_URL`, the
      allowlist vars, and `SEMANTIC_SEARCH_API_TOKEN` if that decision is yes.
- [ ] `mcp-httproute.yaml`: keep the `/sse` and `/messages` rules with `0s` timeouts, and
      add an explicit `/mcp` rule (PathPrefix; this also covers `/mcp/user` later).
- [ ] Optional D2 alias: add a PathPrefix `/mcp` rule to `chatbot/httproute.yaml`
      pointing at backend `sefaria-mcp:8088` (cross-Service backendRef, same namespace).
- [ ] Deploy wiring: either `chatbot-deploy.yml` also bumps `mcp.yaml`'s tag, or
      `release.yaml` dispatches `mcp-deploy.yml` with the chatbot version. Pick one, so
      the two Deployments never run different tool code for long.

### 1.6 Cutover

1. Deploy to dev and verify: `curl /healthz`; claude.ai custom connector **Connect** with
   `https://devmcp.sefaria.org/sse` and with `/mcp`; Claude Desktop; one call to each
   tool; metrics visible in Prometheus.
2. Warn the team that each rollout drops live SSE sessions (single replica, in-memory).
3. Promote to prod, then repeat the checks on `mcp.sefaria.org`.
4. Archive `Sefaria/sefaria-mcp`. Update the wiki pages (`repos/sefaria-mcp/*`) and
   `docs/ARCHITECTURE.md`.

## Phase 2: Sefaria login on `/mcp/user`

Done means: a user adds `https://mcp.sefaria.org/mcp/user` in claude.ai, signs in with
Sefaria, and can list, read and create their own source sheets. `/mcp` and `/sse` are
unchanged.

### Flow

```
MCP client ──POST /mcp/user──► MCP server ──401 + WWW-Authenticate (resource_metadata=…)
   │
   ├─GET mcp.sefaria.org/.well-known/oauth-protected-resource/mcp/user
   │     → { resource: …/mcp/user, authorization_servers: ["https://chat.sefaria.org"] }
   ├─GET chat.sefaria.org/.well-known/oauth-authorization-server   (RFC 8414)
   ├─POST chat.sefaria.org/oauth/register                          (RFC 7591 DCR)
   ├─browser → chat.sefaria.org/oauth/authorize (PKCE S256)
   │     └─302 → www.sefaria.org/api/mcp/authorize?state=…        (login_required)
   │            └─ user logs in (session / Google / Apple) and consents
   │            └─ auto-POST form → chat.sefaria.org/oauth/sefaria/callback
   │                  { state, mcp_token }   ← aud:"mcp" token (D10)
   │     └─302 → client redirect_uri?code=…
   ├─POST chat.sefaria.org/oauth/token  → { access_token, refresh_token }
   └─POST /mcp/user  Authorization: Bearer …  → tools run with the user's Sefaria token
```

### 2.1 Sefaria-Project changes (separate PR, Sefaria team)

- [ ] `GET /api/mcp/authorize`: `@login_required`, with a consent page ("Allow Sefaria AI
      to read and create your source sheets"). It mints an `aud: "mcp"` token
      (`{id, user_id, expiration, aud}`, AES-GCM, same scheme as
      `sefaria/utils/chatbot.py`) and returns it to an **allowlisted** callback with an
      auto-submitting POST form. It is never sent in a query string, because query
      strings get logged.
- [ ] `SessionIDAuthMiddleware`: accept `aud: "mcp"` only on the sheet endpoints the
      tools use, skip CSRF for it the way the admin token does, and leave it rejected
      everywhere else.
- [ ] Choose the TTL (proposal: 30 days) and decide whether revocation is needed
      (proposal: a per-user `mcp_token_version` bump).
- [ ] Sheet endpoint gaps: `GET /api/sheets/<id>` doesn't appear to check privacy, so
      `get_source_sheet` must enforce ownership or public status itself. `/add` and
      `/add_ref` are CSRF-protected and owner-only, which matters if we add an
      "add source to sheet" tool later.

### 2.2 Authorization server in Django (this repo)

- [ ] New app `server/mcp_auth/` with models `OAuthClient` (DCR), `AuthorizationCode`
      (PKCE challenge, short TTL, single use), and `MCPGrant` (sefaria user id, the
      encrypted Sefaria `mcp` token, its expiry, refresh token hash, revoked flag).
      Tokens are stored hashed.
- [ ] Endpoints: `/.well-known/oauth-authorization-server`, `/oauth/register`,
      `/oauth/authorize`, `/oauth/sefaria/callback`, `/oauth/token` (`authorization_code`
      and `refresh_token` grants) and `/oauth/revoke`. PKCE S256 is required. Redirect
      URIs are exact-match. Each `state` is bound to a server-side record.
- [ ] Access tokens are short-lived JWTs (15 min, `aud=https://mcp.sefaria.org/mcp/user`,
      `sub=grant id`), signed with a key held by both processes. The refresh token is
      opaque and rotated. Refresh fails once the Sefaria token has expired, which sends
      the client back through login.
- [ ] Check whether a library (e.g. django-oauth-toolkit) can cover the AS. Use it only
      if it handles DCR and PKCE without much glue code; otherwise keep the hand-rolled
      version minimal.
- [ ] HTTPRoute: `chat[-dev].sefaria.org` already routes `/` to Django, so no infra
      change is needed for the AS.

### 2.3 Resource server in the MCP process

- [ ] Mount a second FastMCP app at `/mcp/user` (stateless) with a JWT verifier
      (`fastmcp` auth provider), and serve the populated
      `/.well-known/oauth-protected-resource/mcp/user`. The public stubs stay as they are
      for `/mcp` and `/sse`.
- [ ] Tools: `get_tools_for_surface("mcp")` plus a new `mcp_user` surface for the three
      sheet tools.
- [ ] Per request, resolve the grant (with Django ORM, since D3 gives the process Django
      settings), decrypt the Sefaria token, and build a per-call `MessageContext` so
      `SefariaToolExecutor` calls `client.set_user_session(sefaria_user_id, token)`. The
      executor is never shared across users.
- [ ] Logging: never log tokens. Metrics get a `surface` label (`public` / `user`).

### 2.4 Tests

- [ ] AS unit tests: DCR, PKCE mismatch, code reuse, expired code, redirect URI mismatch,
      refresh rotation, revoked grant.
- [ ] Resource server: 401 with `WWW-Authenticate` when no token is sent; wrong `aud` is
      rejected; sheet tools appear only on `/mcp/user`; per-user isolation (two
      concurrent users never see each other's token).
- [ ] End-to-end on dev with claude.ai's connector flow and with MCP Inspector.

## Open questions

1. Should `semantic_search` be public, given that it spends a Sefaria token on anonymous
   traffic?
2. Should `validate_refs` and `validate_response_links` be public?
3. Should we add the D2 alias (`chat.sefaria.org/mcp`), or keep mcp.sefaria.org as the
   only MCP host?
4. Sefaria team: the `aud: "mcp"` token, its TTL, and revocation (2.1).
5. When can `/sse` be retired so the Deployment can scale beyond one replica? Check
   traffic split in Prometheus (`http_requests_total` by handler) after cutover.
