# MCP Consolidation: a new Sefaria MCP at chat.sefaria.org/mcp, then user auth

**Date:** 2026-10-18 (revised 2026-10-19: run side by side instead of an in-place cutover)
**Status:** Phase 1 server work in PR #255; Phase 2 implemented, pending review and infra
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
| D10 | Sefaria credential | **Option B (revised 2026-10-20): nothing long-lived reaches Sefaria.** Sefaria proves identity once, at login, with a 5-minute `aud: "mcp-login"` token. For each tool call, the MCP process mints a 5-minute `aud: "mcp"` token itself. Both use a dedicated `MCP_TOKEN_SECRET`, separate from the widget's chatbot secret. Sefaria accepts `aud: "mcp"` only on the three sheet endpoints the tools call, and skips CSRF for it. | The first version of D10 stored a 30-day Sefaria token per user, so a database leak would hand out working Sefaria credentials, and revoking needed a Sefaria-side version bump. The chatbot can already mint widget tokens (AES-GCM is symmetric), so per-call minting adds no trust that doesn't already exist. The dedicated secret and the endpoint allowlist cap what an `mcp` token can do. The long-running credential is our OAuth refresh token, which is rotated, revocable and stored hashed. |

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

- [x] `python -m mcp_server` runs uvicorn on `SEFARIA_MCP_PORT` (8088) and exposes
      metrics on `SEFARIA_MCP_METRICS_PORT` (9090). Metric names and buckets match the old
      server (`mcp_tool_calls_total`, `mcp_tool_duration_seconds`, `mcp_tool_payload_bytes`,
      `mcp_errors_total`). Every series carries `server="chatbot-mcp"`. prometheus_client
      has no const labels, so `server` is an ordinary label that `metrics.labels()`
      fills in. The old server's series have no `server` label. The scrape's
      `job`/`service` target labels also differ, but the explicit label survives
      queries that aggregate those away.
- [x] `serverInfo.version` reports `SEFARIA_MCP_VERSION` (default `0.0.0-dev`).
      Checked on the wire by a test and against a live server. The HelmRelease must set
      it from the image tag (1.5).

### 1.4b Hardening (self-review of PR #255)

- [x] **SSRF in `get_manuscript_image`.** It fetched any caller-supplied URL and
      returned the body base64-encoded, so an anonymous caller could read in-cluster
      URLs (shown live against the pod's own metrics port). It now accepts only
      `https://manuscripts.sefaria.org` (the only host `/api/manuscripts` returns),
      with no redirects, `image/*` content only, and a 10 MB streamed cap. The old
      server on `mcp.sefaria.org` has the same unvalidated fetch; that's left for its
      maintainers or its deprecation.
- [x] Arguments are validated against each tool's JSON schema before execution
      (`jsonschema`, Draft 2020-12), with clean errors (`Invalid arguments for get_text:
      'reference' is a required property`) and `mcp_errors_total{error_type="InvalidArguments"}`.
- [x] Every `size`/`limit` is bounded to 1..`MAX_RESULTS` (100) in the schemas, and
      clamped in the executor for callers that skip validation. `validate_response_links`
      rejects drafts over 100k characters or with more than 50 distinct links (MCP tool
      path only; the hosted agent's own validation is unchanged).
- [x] `test_agent_sdk_mcp.py`: the agent's real SDK tool server round-trips
      initialize, list and call through `claude-agent-sdk`'s bridge on the installed
      `mcp` 2.x, which the fastmcp 4 bump brought into the chatbot image.
- [ ] Not yet done: manuscript images aren't resized (a full scan returns ~7.5M
      characters of base64; the old server downscaled with Pillow); there's no rate or
      concurrency limit on the anonymous endpoint, and its traffic carries a
      first-party `Sefaria/mcp` User-Agent; executor errors still pass raw exception
      text to callers.

### 1.4c Review fixes (external review of #255, 2026-10-21)

- [x] **`validate_response_links` could stall the event loop.** The markdown link pattern was quadratic: "[" * 100,000 took 3.7 s. All patterns are now linear, so worst-case drafts take milliseconds, with a test for each.
- [x] **"Safe to send" missed common link forms.** The MCP tool now also extracts images, autolinks, reference definitions and bare URLs, parses the draft once, and reports `links_checked`. The hosted agent's own check keeps its narrower extraction.
- [x] **`validate_refs` was unbounded.** It is now capped at 50 distinct refs (schema and executor).
- [x] **The SDK floor allowed a broken combination.** `claude-agent-sdk>=0.2.152`, the version tested with `mcp` 2.x.
- [x] CI runs on every pull request, so stacked PRs get checks.
- Not done: manuscript images are still returned as base64 text rather than MCP image content (see 1.4b). Findings about #3801's admin API (concurrent writers, versions that ignore the source ref, uncertain writes audited as failures, editorial review policy) are for that PR's author.

### 1.5 Infrastructure (AppliedAI-Infrastructure)

Nothing under `mcp.yaml`, `mcp-config.yaml`, `mcp-httproute.yaml` or the `mcp` Gateway
changes.

- [ ] New `sefaria-ai/cluster-1/{dev,prod}/chatbot/mcp.yaml`: HelmRelease `chatbot-mcp`,
      `stakater/application`, image `us-docker.pkg.dev/sefaria-ai/container/ai-chatbot-server`
      (same tag as `chatbot.yaml`), command `python -m mcp_server`,
      `SEFARIA_MCP_VERSION` set from the image tag (the way `SENTRY_RELEASE` is in
      `chatbot.yaml`, without its `.tagi` typo), ports 8088/9090,
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

Done means: a user adds `https://chat.sefaria.org/mcp/user` in an MCP client, signs in
with Sefaria, approves access, and can list, read and create their own source sheets.
`/mcp` is unchanged.

### Token contract (both repos build to this)

| Credential | Issued by | Held by | Format | Lifetime | Accepted where |
|---|---|---|---|---|---|
| `mcp-login` token | Sefaria `/api/mcp/authorize` | browser → chatbot callback (auto-POST, never in a URL) | `build_chatbot_user_token` format, `aud: "mcp-login"`, `MCP_TOKEN_SECRET` | 5 min | chatbot `/oauth/sefaria/callback` only |
| Authorization code | chatbot AS | MCP client | random, stored hashed, single use, PKCE S256 | 60 s | chatbot `/oauth/token` |
| Access token | chatbot AS | MCP client | JWT HS256 (`MCP_OAUTH_SIGNING_KEY`); `iss` = chat host, `aud` = `…/mcp/user`, `sub` = grant id, `sefaria_user_id`, `scope: "sheets"` | 15 min | MCP `/mcp/user` |
| Refresh token | chatbot AS | MCP client | random, stored hashed, rotated on use; reusing an old one revokes the grant | 30 days idle, 90 days absolute | chatbot `/oauth/token` |
| `mcp` token | MCP process, per tool call | MCP process → Sefaria `X-Session-ID` | `build_chatbot_user_token` format, `aud: "mcp"`, `MCP_TOKEN_SECRET` | 5 min | Sefaria: `GET /api/sheets/user/<id>…`, `GET /api/sheets/<id>`, `POST /api/sheets/` (CSRF skipped) |

Revoking a grant stops refreshes immediately. An access token already issued keeps
working until it expires (at most 15 minutes), because the MCP process checks the JWT
statelessly and has no database access.

### Flow

```
MCP client ──POST /mcp/user──► chatbot-mcp ──401 + WWW-Authenticate (resource_metadata=…)
   ├─GET /.well-known/oauth-protected-resource/mcp/user          (chatbot-mcp, fastmcp)
   ├─GET /.well-known/oauth-authorization-server                 (Django)
   ├─POST /oauth/register                                        (Django, RFC 7591)
   ├─browser → GET /oauth/authorize (PKCE S256)                  (Django)
   │     sets a browser-binding cookie, then 302 →
   │     www.sefaria.org/api/mcp/authorize?state=…&redirect_uri=<callback>
   │        └─ login_required; auto-POST {state, token: mcp-login} → /oauth/sefaria/callback
   │     callback checks state + binding cookie, decrypts token → GET /oauth/consent
   │     user clicks Allow → 302 client redirect_uri?code=…&state=…
   ├─POST /oauth/token (code + code_verifier) → access + refresh  (Django)
   └─POST /mcp/user  Bearer … → tool mints an `mcp` token → Sefaria sheet API
```

The browser-binding cookie and our own consent page stop login CSRF. Without them, an
attacker could start a flow, get a logged-in victim's browser to complete the Sefaria
step, and receive a grant for the victim's account.

### 2.0 Prototype discovery

- [x] Checked locally that fastmcp 4's `RemoteAuthProvider` with an HS256 `JWTVerifier`
      returns 401 with `WWW-Authenticate: Bearer resource_metadata=…/oauth-protected-resource/mcp/user`,
      serves that document, accepts a valid token (claims reach the tool), and rejects a
      wrong audience.
- [ ] On dev: confirm claude.ai, Claude Desktop and MCP Inspector start OAuth from the
      401 rather than stopping at a root discovery document.

### 2.1 Sefaria-Project (separate PR, stacked on #3801's `aud` support)

- [x] `MCP_TOKEN_SECRET` setting (env), `MCP_AUTH_CALLBACK_URLS` allowlist (settings, local_settings_example, helm configmap).
- [x] `GET /api/mcp/authorize` (`api/mcp_auth_views.py`): requires a real browser session (an `X-Session-ID` user is not enough); validates `state` and that
      `redirect_uri` is allowlisted; responds `no-store` with an auto-submitting POST
      form carrying `state` and an `mcp-login` token.
- [x] `SessionIDAuthMiddleware`: accepts `aud: "mcp"` tokens (MCP secret) only on the
      three sheet routes and methods above, skips CSRF for them, and sets
      `request.auth_via = "mcp"`.
- [x] Tests for both (`sefaria/system/tests/test_middleware.py`, `sefaria/tests/mcp_auth_test.py`): 81 pass, alongside the existing middleware tests. Branch `feature/mcp-user-auth`, stacked on #3801.
- Not in scope: `GET /api/sheets/<id>` doesn't check sheet privacy for any caller.
  That's a Sefaria issue independent of MCP.

### 2.2 Authorization server in Django (this repo, `server/mcp_auth/`)

- [x] Models: `OAuthClient` (DCR), `AuthorizationRequest` (flow state, PKCE challenge,
      browser binding, Sefaria user, code hash), `Grant` (Sefaria user, refresh token
      hashes, idle and absolute expiry, revoked).
- [x] Endpoints: `/.well-known/oauth-authorization-server`, `/oauth/register`,
      `/oauth/authorize`, `/oauth/sefaria/callback`, `/oauth/consent`, `/oauth/token`
      (`authorization_code`, `refresh_token`) and `/oauth/revoke`.
- [x] Redirect URIs must be https, or loopback http for desktop clients, and are matched
      exactly. PKCE S256 is required. `resource` must be our `/mcp/user` URL if given.

### 2.3 Resource server (`mcp_server`)

- [x] `/mcp/user`: a second stateless FastMCP app with `RemoteAuthProvider` and an
      HS256 `JWTVerifier`. It serves the public tools plus the `mcp_user` surface
      (`search_user_source_sheets`, `get_source_sheet`, `create_source_sheet`).
- [x] Each call builds its own `SefariaClient` with a freshly minted `mcp` token, so
      concurrent users never share session state.

### 2.4 Tests

- [x] AS (`mcp_auth/tests/test_oauth.py`, 42 tests): DCR validation, PKCE mismatch, code reuse and expiry, redirect URI mismatch,
      binding-cookie mismatch, refresh rotation and reuse detection, revocation.
- [x] RS (`chat/tests/test_mcp_user_server.py`, 12 tests): 401 + challenge, wrong audience or expiry rejected, sheet tools only on
      `/mcp/user`, minted token format matches Sefaria's decoder, per-user isolation.
- [x] End to end locally (2026-10-20): Sefaria from the branch on :8000, the Django AS
      on :8001, MCP on :18088, and a scripted client and browser. All 19 checks passed:
      discovery, DCR, Sefaria handoff, consent, code exchange, a real sheet created, listed
      and read back as the user, then refresh rotation and reuse revocation. Against the
      real Sefaria middleware, an `mcp` token is refused on sheet delete and `/api/profile`,
      and an `mcp-login` token is not an API credential.

### 2.5 Rollout (not done)

- [ ] Secrets: generate `MCP_TOKEN_SECRET` (shared by Sefaria, chatbot Django and
      chatbot-mcp) and `MCP_OAUTH_SIGNING_KEY` (chatbot Django and chatbot-mcp). Add
      `MCP_AUTH_CALLBACK_URLS=https://chat[-dev].sefaria.org/oauth/sefaria/callback` on
      Sefaria.
- [ ] Infra: route `/oauth/*` and `/.well-known/oauth-authorization-server` on the chat
      host to Django (already covered by `/`), and `/mcp/user` plus
      `/.well-known/oauth-protected-resource/mcp/user` to chatbot-mcp (covered by the 1.5
      PathPrefix rules).
- [ ] Dev: connect claude.ai, Claude Desktop and MCP Inspector to
      `https://chat-dev.sefaria.org/mcp/user` (2.0's open check).
- Later: a page where users can see and revoke their grants; rate limits on
  `/oauth/register` and `/oauth/token`; deleting expired grants.

### 2.6 Review fixes (external review, 2026-10-21)

An external review of #256 and #3840 found six issues. The three high-severity ones are fixed; the rest are deferred, by choice, to keep the change small.

- [x] **The `mcp` token was not create-only.** `POST /api/sheets/` also updates sheets (payload `id`) and publishes them (`status`). For `auth_via == "mcp"`, Sefaria now refuses an `id`, forces `status: unlisted` and drops `displayedCollection` (#3840).
- [x] **Replay detection forgot older refresh tokens.** Only the previous hash was kept. Every rotated-out token is now recorded (`ConsumedRefreshToken`); a replay or revoke of any of them revokes the grant (RFC 9700 4.14.2); and the token is re-checked under the row lock.
- [x] **Anonymous registration grew the DB without limit.** Registration bodies are capped at 8 KB and redirect URIs at 2,000 characters; there are global ceilings of 500 new clients an hour and 2,000 sign-ins in progress (429); clients a day old with no completed sign-in are deleted. Per-IP limits are left to the gateway.
- [x] Also found: pytest only collected `chat/`, so CI never ran the `mcp_auth` tests. `testpaths` now lists `mcp_auth`.
- Deferred: `create_source_sheet` has no source-count bound and fans out reference fetches; Sefaria errors returned as HTTP 200 become successful tool results; a grant-management page; separate read and write scopes. (The handoff token's `state` binding, first deferred here, is fixed in 2.8.)

### 2.7 Staff tools on `/mcp/admin` (2026-10-21)

The topic-prompt tools move here from the stdio `sefaria-admin-mcp` server, and the separate admin token flow is retired. There is one sign-in flow for every signed-in endpoint.

- [x] `list_topic_prompts` and `update_topic_prompt` are chatbot tool schemas on a new `mcp_admin` surface. They call Sefaria's `/api/admin/topic-prompts` API as the signed-in user, with the same per-call 5-minute `mcp` token as the sheet tools. Sefaria's error message is kept, and a 409 includes the current version.
- [x] `/mcp/admin` serves only those two tools, with tool annotations and server instructions carried over from the stdio server. It is its own OAuth resource: the authorization server issues the `admin` scope only for `/mcp/admin` (`MCP_ADMIN_RESOURCE_URL`) and `sheets` only for `/mcp/user`, with matching token audiences, so a token is never accepted at the other endpoint. The consent page names what is being approved.
- [x] **Decision: staff access is enforced by Sefaria, not here.** Anyone can sign in to `/mcp/admin`; Sefaria accepts `mcp` tokens on the two topic-prompt routes only for `is_staff` users, and the views check again. Grants follow the normal lifetimes (90 days at most) instead of the old 12-hour token; that was accepted on purpose.
- [x] Sefaria (#3840's branch): removed the `/api/admin/mcp-token` minting page, `ADMIN_MCP_TOKEN_SECRET` and the `admin-mcp` audience. Audit records now say `via: "mcp"` or `"session"`.
- Not done: the stdio server's `review_topic_prompts` MCP prompt was not ported. The `Sefaria/sefaria-admin-mcp` repo is now unused and can be archived. `MCP_ADMIN_RESOURCE_URL` defaults to the `/mcp/admin` beside `MCP_USER_RESOURCE_URL`, since the MCP host can differ from the issuer (the Coolify test app first failed client registration because the default was the issuer host).

### 2.8 Pre-merge review fixes (2026-10-22)

A code review of #256 and #3840 before merging found these; all but one are fixed.

- [x] **The `mcp-login` token is bound to its flow.** Sefaria puts the flow's `state` in the token, and the callback accepts it only for that state, so a captured token cannot complete another sign-in (it was replayable for 5 minutes).
- [x] `scope=admin` without RFC 8707's `resource` now targets `/mcp/admin` instead of failing.
- [x] Grants past their 90-day absolute expiry are deleted (with their consumed refresh tokens) when a new grant is issued, so neither table grows forever.
- [x] The topic-prompt tool schemas reject unknown arguments (they used to crash with a `TypeError`), and `topic` is a plain list of strings instead of a string-or-list type some clients mishandle.
- [x] Sefaria: `POST /api/sheets/` with a non-object JSON body is a 400, not a 500.
- [x] **Cross-site return (2026-10-22).** The browser-binding cookie was `SameSite=Lax`, which browsers drop from Sefaria's cross-site auto-POST when Sefaria is on another site than the chat host. Staging is exactly that (`chat-dev.sefaria.org` with `sefariastaging.org`), so every staging sign-in would fail with "Finish signing in from the same browser". Over https the cookie is now `SameSite=None; Secure`; that is safe because the cookie only proves which browser started the flow, and an attacker's flow is bound to the attacker's own cookie. Plain-http local development keeps `Lax`.

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
