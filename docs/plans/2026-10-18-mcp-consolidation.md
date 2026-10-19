# MCP Consolidation: a new Sefaria MCP at chat.sefaria.org/mcp, then user auth

**Date:** 2026-10-18 (revised 2026-10-19: run side by side instead of an in-place cutover)
**Status:** Proposed
**Branch:** `feat/mcp-consolidation` (off `main`)
**Supersedes:** `docs/superpowers/specs/2026-09-03-mcp-consolidation-design.md` on the
unmerged `local-agent-thin` branch. Phase 1 reuses most of that work. Where this plan
differs from it, the reason is given.

## Goal

1. **Phase 1, new endpoint.** `https://chat.sefaria.org/mcp` (dev:
   `chat-dev.sefaria.org/mcp`) serves a public, authless MCP from this repo. It uses the
   same `SefariaToolExecutor` the Library Assistant uses.
2. **Phase 2, auth.** `https://chat.sefaria.org/mcp/user` requires Sefaria login (OAuth,
   per the MCP authorization spec) and adds the source-sheet tools.
3. **Later, deprecation.** Once clients have moved, `mcp.sefaria.org` /
   `devmcp.sefaria.org` and `Sefaria/sefaria-mcp` are retired.

**The old MCP is not touched by this work.** The `mcp` HelmRelease, its routes and
hosts, and the sefaria-mcp repo and image (`container/sefaria-mcp`) keep running as
they are until the deprecation step. Because nothing switches over in place, the new
endpoint only has to be good, not identical to the old one.

## Where things stand

| | Today |
|---|---|
| Old public MCP | `Sefaria/sefaria-mcp`, a FastMCP app with a single replica on the AI cluster. HelmRelease `mcp` lives in `AppliedAI-Infrastructure/sefaria-ai/cluster-1/{dev,prod}/mcp.yaml`, with Service `sefaria-mcp` on port 8088 and metrics on 9090. Hosts: `devmcp.sefaria.org` / `mcp.sefaria.org`. It serves `/mcp` (stateless Streamable HTTP) and `/sse`, plus the empty OAuth well-known stubs. It has 14 tools; prod is older and also has a broken `english_semantic_search`. |
| Chatbot | Django, WSGI (`gunicorn gthread`), 3 replicas in prod. HelmRelease `chatbot` with Service `chatbot` on 8080. Hosts: `chat-dev` / `chat.sefaria.org`, with Gateway `chatbot` and HTTPRoute `chatbot/httproute.yaml` (`/` → 8080; `.*/stream` with `0s` timeouts; a websocket rule). No ServiceMonitor. |
| Chatbot tools | `chat/V2/agent/tool_schemas.py`, `tool_executor.py`, `sefaria_client.py` (httpx, Django-free). Source-sheet tools (`search_user_source_sheets`, `get_source_sheet`, `create_source_sheet`) are Labs-only. They authenticate to Sefaria by sending the user's encrypted chatbot token as `X-Session-ID`. |

## Decisions

| # | Decision | Choice | Why |
|---|---|---|---|
| D1 | Hosting | **One repo and one codebase, deployed as its own Deployment** (new HelmRelease `chatbot-mcp`). | This keeps public MCP traffic off the chatbot's capacity and failure path (the chatbot ships to every Sefaria user), and the chatbot stays WSGI. "Serving MCP from the chatbot server" means the same host and codebase, not the same pods. |
| D2 | Host | **`chat[-dev].sefaria.org/mcp`**, path-routed on the existing `chatbot` HTTPRoute to the `chatbot-mcp` Service. | No new DNS, certificate or Gateway listener. It also sits on the same host as the Phase 2 authorization server (D9). A separate hostname was considered and rejected: it adds infrastructure and is one more host to retire later. |
| D3 | Image | **The chatbot image, run with a different command** (`python -m mcp_server`). The separate `Dockerfile.mcp` and its `build_mcp` release job from b67f577b were removed. | That job pushed to `container/sefaria-mcp`, the **old server's image repo**, with `latest=true`, so it would have disturbed the MCP we are leaving alone. Phase 2 also needs Django settings and secrets in the MCP process (D9). |
| D4 | Tool selection | The `surfaces` marker on each schema (from `de89ee76`). Agent-only is the default. | This extends the `LABS_TOOL_NAMES` pattern, and forgetting the marker can't leak a tool. |
| D5 | Tool names | LA names, no aliases (`text_search` becomes `specific_keyword_search`). | It's a new endpoint, so there is no name contract to keep. |
| D6 | OAuth discovery (Phase 1) | **Measure, then decide.** On dev, test claude.ai's settings **Connect**, Claude Desktop and MCP Inspector against two variants: (a) no discovery documents at all, the spec-correct choice, and (b) an empty stub at `/.well-known/oauth-protected-resource/mcp`. Ship whichever connects everywhere. | On 2026-08-17, 404s broke claude.ai Connect on the old server and stubs worked (sefaria-mcp PRs #25 → #28; wiki `repos/sefaria-mcp/oauth-discovery`). b67f577b assumed the opposite. A fresh endpoint lets us test this without risk. Note that `chat.sefaria.org/.well-known/oauth-*` at the root is answered by Django (404 today, and the Phase 2 AS metadata later), so only the path-specific document is ours to vary. |
| D7 | Transport | **Streamable HTTP only, `stateless_http=True`. No SSE.** Host/Origin protection is ported from sefaria-mcp `main`. | SSE clients stay on the old server until it's deprecated. Without SSE there is no in-memory session state, so the Deployment can run 2+ replicas and survive rollouts. |
| D8 | Auth topology | **Two endpoints.** `/mcp` is public and authless. **`/mcp/user`** requires OAuth and serves the public tools plus the sheet tools. | MCP clients only start OAuth when they get a 401, so optional auth on one endpoint would never prompt a login. A separate authenticated endpoint keeps anonymous use frictionless. |
| D9 | Authorization server | **The Django chatbot app on `chat.sefaria.org` is the OAuth authorization server.** It gets Sefaria identity through a login handoff on sefaria.org. The MCP process is a resource server that validates the access tokens. | Sefaria is not an OAuth server: it is only an allauth client of Google and Apple, and it has no authorize, token or well-known endpoints. Our Postgres already holds users and can store clients, codes and tokens. Building an AS into Sefaria-Project is a much larger change. |
| D10 | Sefaria credential | Sefaria mints a **new `aud: "mcp"` token** for the user, scoped to the sheet APIs and with a longer TTL. Django stores it encrypted, and MCP sends it as `X-Session-ID`. The chatbot never mints Sefaria tokens itself. | This mirrors the existing admin MCP token (`AdminMCPTokenView`, `SessionIDAuthMiddleware` audience check). If the chatbot minted tokens from the shared secret, it would hold impersonate-anyone power for long-lived sessions. |

## Phase 1: public MCP at `chat.sefaria.org/mcp`

Done means: `https://chat.sefaria.org/mcp` connects from claude.ai, Claude Desktop and
MCP Inspector, every tool call succeeds, and metrics are scraped. The old MCP is
unchanged.

### 1.1 Bring the prior work onto `main`

- [x] Cherry-picked from `local-agent-thin`: `de89ee76` (surfaces), `b67f577b` (server),
      `105cdcc8` (link validation), `0780e0ae` (lazy `chat/V2/agent/__init__.py`),
      `c7ed355c` (MCP tests) and `edf0fbe` (ARCHITECTURE tool inventory). Everything
      applied cleanly except `edf0fbe`, whose edit to the September spec was dropped
      because this plan supersedes it.
- [x] `mcp_server.app` builds 21 tools with no Django, `claude_agent_sdk`, `anthropic`
      or `braintrust` in `sys.modules`.
- [x] `test_mcp_server.py` and `test_validate_response_links_tool.py` pass (23 tests).
      The full suite is unchanged from `main` apart from the new tests. Its 133 SQLite
      setup errors are pre-existing; the fix is on the separate branch
      `fix/sqlite-safe-migrations`.
- [x] D3: removed `Dockerfile.mcp`, `requirements-mcp.txt` and the `build_mcp` job. The
      MCP dependencies (`fastmcp`, `uvicorn`, `prometheus-client`) are now in
      `server/requirements.txt`.

### 1.2 Server changes

- [x] Dropped the `/sse` transport (D7). `/mcp` only, `stateless_http=True`, no
      `mcp-session-id` issued.
- [x] Host/Origin guard with `SEFARIA_MCP_ALLOWED_HOSTS` / `SEFARIA_MCP_ALLOWED_ORIGINS`
      (defaults: `chat.sefaria.org`, `chat-dev.sefaria.org` and their https origins;
      loopback is always admitted by FastMCP), plus `SEFARIA_MCP_HOST_PROTECTION=off`.
      Blank values fall back to the defaults. Unlisted Host gets 421, unlisted Origin
      gets 403.
- [x] `SEFARIA_MCP_DISCOVERY=stub|none` (default `stub`, the version proven on
      claude.ai) controls `/.well-known/oauth-protected-resource/mcp`. `resource` comes
      from `SEFARIA_MCP_PUBLIC_URL` (default `https://chat.sefaria.org/mcp`). The
      host-root `/.well-known/*` paths are left to Django.
- [x] `/mcp/healthz`, outside the guard: kubelet probes send the pod IP as Host, which
      the guard would reject with 421.
- [x] `fastmcp>=4.0.3,<5` (4.1.1 installed, which brings `mcp` 2.x). `pip check` is
      clean next to `claude-agent-sdk`.
- [x] Transport tests ported from sefaria-mcp (`test_mcp_server.py`): allowed and
      unlisted Host/Origin, env-driven allowlists, the escape hatch, statelessness,
      405-not-404 for GET/PUT/PATCH, no SSE routes, healthz, both discovery modes, and
      env parsing.
- [x] Fixed: every live tool call failed with "settings are not configured". The
      User-Agent helper on `main` (`580b0a01`) reads `settings.ENVIRONMENT`, and the MCP
      process doesn't configure Django. The unit tests missed it because the suite
      always configures Django and mocks the HTTP client. `_user_agent()` now reads
      `ENVIRONMENT` from the environment when settings aren't configured, and
      `SEFARIA_USER_AGENT_SERVICE=mcp` makes MCP traffic send `Sefaria/mcp (<env>)`.
      Guarded by `test_tools_run_without_django_settings`, a subprocess test without
      `DJANGO_SETTINGS_MODULE` (confirmed to fail without the fix).
- [x] Live smoke test of `python -m mcp_server`: initialize, `tools/list` (21),
      real calls to `get_text`, `specific_keyword_search`, `get_current_calendar` and
      `catalog_search`, the discovery document, and Prometheus counters.

### 1.3 Public tool list

The `mcp` surface:

| Tool | Notes |
|---|---|
| `get_text` | |
| `specific_keyword_search` | |
| `semantic_search` | calls `www.sefaria.org/api/knn-search` and needs `SEMANTIC_SEARCH_API_TOKEN`. **Decision needed:** whether to spend that token on anonymous traffic. If not, leave the tool off the surface. |
| `get_current_calendar` | |
| `get_links_between_texts` | |
| `search_in_book`, `search_in_dictionaries` | |
| `get_english_translations` | |
| `get_topic_details` | |
| `clarify_name_argument`, `clarify_search_path_filter` | |
| `catalog_get_node`, `catalog_get_children`, `catalog_search`, `catalog_query` | |
| `get_available_manuscripts`, `get_manuscript_image` | |
| `validate_refs`, `validate_response_links` | confirm we want them public |

- [x] Dropped `get_text_or_category_shape` and `get_text_catalogue_info`: their schemas,
      dispatch branches, the `SefariaClient` methods and the now-unused
      `_optimize_index_response`. `catalog_*` covers them.
      `latency/scripts/plot_latency_partition.py` keeps the names, which classify
      historical traces.
- [x] `test_public_surface_is_exactly_the_reviewed_set` pins the 19 public tools, so
      adding or removing one is a deliberate change.
- `semantic_search`, `validate_refs` and `validate_response_links` stay public for now
  (open questions 1 and 2).

Not public: `search_user_source_sheets`, `get_source_sheet`, `create_source_sheet`.
They go on `/mcp/user` in Phase 2.

### 1.4 Run and observe

- [ ] `python -m mcp_server` runs uvicorn on `SEFARIA_MCP_PORT` (8088) and exposes
      metrics on `SEFARIA_MCP_METRICS_PORT` (9090). Metric names are kept
      (`mcp_tool_calls_total`, etc.), with a `server="chatbot-mcp"` const label so they
      don't merge with the old server's series in Prometheus.
- [ ] `SEFARIA_MCP_VERSION` is set from the image tag in the HelmRelease, so
      `serverInfo.version` reports our release.

### 1.5 Infrastructure (AppliedAI-Infrastructure)

Nothing under `mcp.yaml`, `mcp-config.yaml`, `mcp-httproute.yaml` or the `mcp` Gateway
changes.

- [ ] New `sefaria-ai/cluster-1/{dev,prod}/chatbot/mcp.yaml`: HelmRelease `chatbot-mcp`,
      `stakater/application`, image `us-docker.pkg.dev/sefaria-ai/container/ai-chatbot-server`
      (same tag as `chatbot.yaml`), command `python -m mcp_server`, ports 8088/9090,
      ServiceMonitor on, `replicas: 2` in prod, probes on `/mcp/healthz`. Env from a new
      ConfigMap (`SEFARIA_MCP_PUBLIC_URL`, the allowlists, `SEFARIA_API_BASE_URL`,
      `ENVIRONMENT`, `SEFARIA_USER_AGENT_SERVICE=mcp`, `SEFARIA_MCP_DISCOVERY`) and,
      if `semantic_search` is public, a secret.
- [ ] `chatbot/httproute.yaml`: add a PathPrefix `/mcp` rule → `chatbot-mcp:8088` above
      the `/` catch-all, plus `/.well-known/oauth-protected-resource/mcp` (PathPrefix, so
      `/mcp/user` is covered in Phase 2) → `chatbot-mcp:8088`. Streamable HTTP responses
      can stream, so give the `/mcp` rule `0s` timeouts like `.*/stream`.
- [ ] Add the file to `chatbot/kustomization.yaml`.
- [ ] Deploy wiring: `chatbot-deploy.yml` bumps the tag in both `chatbot.yaml` and
      `mcp.yaml` in the same commit, so the chatbot and the MCP never run different tool
      code.

### 1.6 Roll out

1. Deploy to dev and run the D6 measurement: Connect from claude.ai (settings and chat
   connector flow), Claude Desktop, and MCP Inspector against
   `https://chat-dev.sefaria.org/mcp`, with discovery set to `none` and then to `stub`.
   Record the result in the plan and the wiki (`repos/sefaria-mcp/oauth-discovery`).
2. Call each tool once and check metrics in Prometheus.
3. Promote to prod and repeat the checks on `chat.sefaria.org/mcp`.
4. Update `docs/ARCHITECTURE.md` and the wiki with the new endpoint.

## Phase 2: Sefaria login on `/mcp/user`

Done means: a user adds `https://chat.sefaria.org/mcp/user` in claude.ai, signs in with
Sefaria, and can list, read and create their own source sheets. `/mcp` is unchanged.

### Flow

Everything is on `chat.sefaria.org`. The gateway routes `/mcp*` and
`/.well-known/oauth-protected-resource/mcp*` to `chatbot-mcp`; everything else goes to
Django.

```
MCP client ──POST /mcp/user──► chatbot-mcp ──401 + WWW-Authenticate (resource_metadata=…)
   │
   ├─GET /.well-known/oauth-protected-resource/mcp/user        (chatbot-mcp)
   │     → { resource: https://chat.sefaria.org/mcp/user,
   │         authorization_servers: ["https://chat.sefaria.org"] }
   ├─GET /.well-known/oauth-authorization-server               (Django, RFC 8414)
   ├─POST /oauth/register                                      (Django, RFC 7591 DCR)
   ├─browser → /oauth/authorize (PKCE S256)                    (Django)
   │     └─302 → www.sefaria.org/api/mcp/authorize?state=…    (login_required)
   │            └─ user logs in (session / Google / Apple) and consents
   │            └─ auto-POST form → /oauth/sefaria/callback   (Django)
   │                  { state, mcp_token }   ← aud:"mcp" token (D10)
   │     └─302 → client redirect_uri?code=…
   ├─POST /oauth/token  → { access_token, refresh_token }      (Django)
   └─POST /mcp/user  Authorization: Bearer …  → tools run with the user's Sefaria token
```

### 2.0 Prototype discovery first

- [ ] Before building the AS, deploy a dev-only `/mcp/user` that returns 401 with a
      populated path-specific protected-resource document pointing at a stub AS
      metadata document. Confirm that claude.ai, Claude Desktop and MCP Inspector all
      start an OAuth flow, i.e. none of them stops at the root discovery document. This
      is the riskiest assumption in Phase 2.

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
- [ ] Access tokens are short-lived JWTs (15 min, `aud=https://chat.sefaria.org/mcp/user`,
      `sub=grant id`), signed with a key held by both processes. The refresh token is
      opaque and rotated. Refresh fails once the Sefaria token has expired, which sends
      the client back through login.
- [ ] Check whether a library (e.g. django-oauth-toolkit) can cover the AS. Use it only
      if it handles DCR and PKCE without much glue code; otherwise keep the hand-rolled
      version minimal.
- [ ] Routing: the `/`, `/oauth/*` and root `/.well-known/oauth-authorization-server`
      paths already go to Django, so no infra change is needed beyond 1.5.

### 2.3 Resource server in the MCP process

- [ ] Mount a second FastMCP app at `/mcp/user` (stateless) with a JWT verifier
      (`fastmcp` auth provider), and serve the populated
      `/.well-known/oauth-protected-resource/mcp/user`.
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

## Deprecating the old MCP (after Phase 1 is proven)

- [ ] Announce `chat.sefaria.org/mcp` as the Sefaria MCP URL (docs, developer pages,
      README of sefaria-mcp).
- [ ] Watch old-server traffic in Prometheus (`mcp_tool_calls_total` without
      `server="chatbot-mcp"`) until it's low enough to retire.
- [ ] Then choose: 301 `mcp.sefaria.org` to the new endpoint (this covers Streamable
      HTTP clients only; SSE clients must reconfigure) or switch it off. Remove the `mcp`
      HelmRelease and routes, and archive `Sefaria/sefaria-mcp`.

## Open questions

1. Should `semantic_search` be public, given that it spends a Sefaria token on anonymous
   traffic?
2. Should `validate_refs` and `validate_response_links` be public?
3. Sefaria team: the `aud: "mcp"` token, its TTL, and revocation (2.1).
4. Deprecation: redirect `mcp.sefaria.org` or turn it off, and on what timeline?
