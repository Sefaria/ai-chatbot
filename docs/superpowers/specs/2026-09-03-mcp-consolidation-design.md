# Consolidating sefaria-mcp into ai-chatbot

**Date:** 2026-09-03
**Status:** Approved design, not yet implemented

## Problem

Sefaria runs two independent implementations of the same Sefaria tool surface:

- `Sefaria/sefaria-mcp` — a standalone FastMCP server on the AI cluster, serving
  `mcp.sefaria.org` and `devmcp.sefaria.org` to public MCP clients.
- `ai-chatbot` — the Library Assistant's in-process tool layer, used by the Claude
  Agent SDK.

The standalone repo is effectively unmaintained. Its deploy dispatch is broken and
deploys are manual, which has produced real, currently-live defects (see Assessment).
Keeping two hand-synced implementations of the same tools is the root cause.

This spec consolidates the public MCP server into `ai-chatbot`, so that one tool
implementation serves both surfaces, and `Sefaria/sefaria-mcp` can be archived.

## Assessment of the live setup (verified 2026-09-03)

Measured directly against both hosts, not taken from documentation.

| | `mcp.sefaria.org` (prod) | `devmcp.sefaria.org` |
|---|---|---|
| `/sse` | 200 — the only working transport | 200 |
| `/mcp` (Streamable HTTP) | 404 | 404 |
| `/.well-known/oauth-*` | 200 (stubs) | 200 (stubs) |
| Negotiated protocolVersion | 2025-11-25 | 2025-11-25 |
| `serverInfo.version` | 3.4.2 | 3.4.7 |
| Tools exposed | 15 | 14 |

Findings:

1. **Every existing connector uses `https://mcp.sefaria.org/sse` over SSE.**
   Streamable HTTP is not served at all.
2. **Prod is running pre-July code.** It still exposes `english_semantic_search`,
   which was deleted in PR #22 (`56d5f8f`). Calling it returns
   `Failed to resolve 'ai.sefaria.org'` — the prod cluster cannot resolve the
   internal host that tool targets. Public semantic search has been broken for
   months. The LA's `semantic_search` calls `https://www.sefaria.org/api/knn-search`
   instead, so migration repairs this.
3. **The OAuth well-known stubs are live on both hosts.** PR #25, which removed
   them, was never deployed anywhere — contradicting the wiki, which records it as
   live on dev. Prod additionally serves `"resource": "https://devmcp.sefaria.org"`,
   advertising the dev hostname as prod's own resource identity.
4. **Prod and dev run different FastMCP versions**, because `fastmcp` is an unpinned
   bare dependency installed at image build time. This is also why
   `serverInfo.version` reports FastMCP's version rather than the release tag.
5. `serverInfo.version` will continue to be FastMCP's version unless set explicitly.

## Constraints

- **Setup compatibility is the requirement.** Existing connector users must not have
  to reconfigure anything. Tool *names* are explicitly not part of the contract —
  the model on the other end adapts.
- **Bina / `slack-mcp` is deprecated** and is not a consumer to coordinate with.
- **DNS is handled outside this work.** Cloudflare changes to point
  `mcp.sefaria.org` at the new service are the user's; everything else is in scope.
- **Authless.** No authentication is being added.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Hosting | Separate process and image, same repo | Keeps public MCP traffic off the chatbot's capacity and failure path, while collapsing two repos into one |
| Tool selection | Declarative `"surfaces"` marker on each schema | Extends the existing `LABS_TOOL_NAMES` pattern rather than inventing a parallel one |
| Tool vocabulary | LA names, single canonical set, no aliases | Names are not part of the contract |
| Package layout | Files stay put; `__init__.py` made lazy | A move would churn ~150 references, mostly `mock.patch` strings, for no functional gain |
| Transport | Streamable HTTP at `/mcp` **and** SSE at `/sse` | `/sse` is what every existing connector uses; SSE is deprecated in the spec but still supported by FastMCP |

### Public tool surface

Marked for the `mcp` surface — the LA's general tools:

`get_text`, `semantic_search`, `get_current_calendar`, `specific_keyword_search`,
`get_links_between_texts`, `search_in_book`, `search_in_dictionaries`,
`get_english_translations`, `validate_refs`, `get_topic_details`,
`clarify_name_argument`, `clarify_search_path_filter`, `catalog_get_node`,
`catalog_get_children`, `catalog_search`, `catalog_query`,
`get_available_manuscripts`, `get_manuscript_image`.

**Not** marked: `search_user_source_sheets`, `get_source_sheet`,
`create_source_sheet` — these require a per-user token via `MessageContext`, which an
authless server cannot supply.

**Dropped** relative to today's public server: `get_text_or_category_shape` and
`get_text_catalogue_info`. The LA's cached `catalog_*` tools supersede them.
(`SefariaClient` retains both methods as dead code; removing them is optional cleanup.)

## Architecture

```
                    server/chat/V2/agent/   (shared, Django-free)
                    tool_schemas.py  ← "surfaces" marker
                    tool_executor.py
                    sefaria_client.py
                    catalog_service.py
                          ▲                        ▲
                          │                        │
        claude_service.py │                        │ server/mcp_server/app.py
        (Claude Agent SDK)│                        │ (FastMCP, /mcp + /sse)
                          │                        │
                   Django web app            public MCP container
```

The tool layer already imports with no `DJANGO_SETTINGS_MODULE` set; it depends only
on `httpx` and stdlib.

## Components

### 1. Surface marker (`tool_schemas.py`)

Each `TOOL_*` dict gains a `"surfaces"` tuple, defaulting to agent-only when absent:

```python
TOOL_GET_TEXT = {
    "name": "get_text",
    "surfaces": ("agent", "mcp"),
    "description": ...,
    "input_schema": ...,
}
```

Add `get_tools_for_surface(surface: str) -> list[dict]` beside the existing
`get_tools_for_labs()`. Existing selectors keep their current behaviour.

### 2. Lazy package init (`chat/V2/agent/__init__.py`)

Today this eagerly imports `claude_service`, so any import of the tool layer also
pulls in `claude_agent_sdk`, `anthropic`, and `braintrust`. Replace the eager
re-exports with a module-level `__getattr__` that imports on first attribute access.
Public API is unchanged; the MCP process imports only the pure modules.

### 3. MCP server (`server/mcp_server/`)

- `tool.py` — `SefariaTool(fastmcp.tools.base.Tool)`. `Tool` carries
  `parameters: dict[str, Any]` (a raw JSON schema) and an abstract
  `async def run(arguments) -> ToolResult`, so each schema's `input_schema` becomes
  `parameters` directly and `run()` delegates to a shared `SefariaToolExecutor`.
  No dynamically generated typed signatures are needed.
- `app.py` — builds the `FastMCP` instance, registers one `SefariaTool` per entry in
  `get_tools_for_surface("mcp")`, mounts both transports, sets
  `app.router.redirect_slashes = False` (the old server does this; `/sse` must not
  redirect), and sets `serverInfo.version` to the release tag rather than FastMCP's.
- `metrics.py` — Prometheus `calls` / `duration` / `payload_bytes` / `errors`,
  labelled by `tool_name`, keeping today's metric names so existing Grafana
  dashboards survive the cutover. Bound on `SEFARIA_MCP_METRICS_PORT` (default
  `9090`), separate from the MCP port (`SEFARIA_MCP_PORT`, default `8088`).
- **No `/.well-known/oauth-*` routes.** Under RFC 9728, absence means "no auth
  required"; the empty stubs are what convinced claude.ai the server was
  OAuth-protected. This is a deliberate, tested behaviour change.

### 4. Packaging and CI

- `server/requirements-mcp.txt` — `httpx`, `fastmcp` (**pinned**), `prometheus-client`,
  `uvicorn`. No Django, `anthropic`, `braintrust`, or Claude Code CLI.
- A separate `Dockerfile.mcp`, rather than another stage in the existing Dockerfile,
  which builds the Svelte bundle and the full Django image. Entrypoint is uvicorn.
- `release.yaml` gains a second image build/push. Semantic-release already runs here.

## Error handling

`SefariaToolExecutor.execute` already catches exceptions and returns
`ToolResult(is_error=True)`, so tool failures surface as MCP tool errors rather than
protocol errors. Preserve that: no exception escapes `SefariaTool.run`. Upstream
Sefaria API failures remain per-call errors and must not take the server down.

## Testing

- **Marker invariant:** every `mcp`-marked tool has a `_dispatch` branch in
  `SefariaToolExecutor` and requires no `MessageContext`. This is what prevents an
  auth-requiring tool from being marked by accident.
- **Surface listing:** `list_tools` over an in-memory FastMCP client returns exactly
  the marked set.
- **Round-trip:** each marked tool executes against a mocked `SefariaClient` through
  the full MCP path.
- **`/.well-known/oauth-protected-resource` and `/.well-known/oauth-authorization-server`
  return 404.**
- **`/sse` does not redirect.**
- Existing `test_tool_executor.py`, `test_sefaria_client.py`, and
  `test_catalog_service.py` continue to cover tool logic; MCP-level tests stay thin.

## Verification before cutover

Stand the new service up on a temporary hostname and, against it and
`mcp.sefaria.org` side by side:

1. Complete an SSE handshake at `/sse`; confirm the negotiated protocol version and
   that older versions (2024-11-05, 2025-03-26, 2025-06-18) still negotiate.
2. Complete a Streamable HTTP handshake at `/mcp`.
3. Diff `list_tools` output against the expected marked set.
4. Run representative calls (`get_text`, `specific_keyword_search`, `semantic_search`,
   `catalog_search`, `get_manuscript_image`) and compare results — `semantic_search`
   is expected to *differ*, because prod's is broken.
5. Add the temporary hostname as a real claude.ai custom connector and confirm setup
   completes with no OAuth prompt.
6. Confirm Prometheus metric names and labels match today's.

## Out of scope

- Cloudflare DNS changes pointing `mcp.sefaria.org` at the new service.
- Archiving `Sefaria/sefaria-mcp` (after DNS has moved and soaked).
- Adding authentication.
- Removing the now-dead `get_text_or_category_shape` / `get_text_catalogue_info`
  methods from `SefariaClient`.
