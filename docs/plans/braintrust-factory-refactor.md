# Braintrust & Agent Service Refactor Plan — IMPLEMENTED

## Problem 1: Remove scattered if-else from Braintrust tracing

### Root cause
`_send_message_inner` holds a `bt_span` reference initialized conditionally:
```python
bt_span = current_span() if self.braintrust_logging_enabled else None
```
Then every logging site guards against `None`:
```python
if bt_span:
    bt_span.log(...)
```

### Solution: Rely on Braintrust no-op semantics

When `init_logger()` is never called, the Braintrust SDK provides:
- `current_span()` → returns a **noop span** (safe to call methods on)
- `span.log(...)` → no-op on noop span
- `span.start_span(...)` → returns another noop span
- `@braintrust.traced` → decorator is a no-op
- `braintrust.flush()` → no-op

So the only conditional that must remain is at the **initialization point**.

### Changes

#### `server/chat/V2/agent/claude_service.py`

1. **`send_message`** — remove `braintrust_logging_enabled` guard, always use `@traced`:
   ```python
   # REMOVE this block:
   if not self.braintrust_logging_enabled:
       return await message_task
   ```
   Just always define and call `run()` with `@braintrust.traced`.

2. **`_send_message_inner`** — remove conditional on `bt_span`:
   ```python
   # BEFORE:
   bt_span = current_span() if self.braintrust_logging_enabled else None
   # AFTER:
   bt_span = current_span()
   ```

3. **All `if bt_span:` guards** — remove them, call `.log()` directly:
   ```python
   # BEFORE:
   if bt_span:
       bt_span.log(input=span_input, metadata=span_metadata)
   # AFTER:
   bt_span.log(input=span_input, metadata=span_metadata)
   ```
   Affected lines: ~313, ~358, ~399, ~461

4. **`_run_guardrail`** — remove conditional span creation:
   ```python
   # BEFORE:
   guardrail_span = bt_span.start_span(name="guardrail", type="task") if bt_span else None
   if guardrail_span:
       guardrail_span.log(...)
       guardrail_span.end()
   if bt_span:
       bt_span.log(output={"content": rejection, ...})
   ...
   trace_id=bt_span.id if bt_span else None,
   # AFTER:
   guardrail_span = bt_span.start_span(name="guardrail", type="task")
   guardrail_span.log(...)
   guardrail_span.end()
   bt_span.log(output={"content": rejection, ...})
   ...
   trace_id=bt_span.id,
   ```

5. **`trace_id` fallback** — simplify:
   ```python
   # BEFORE:
   if not trace_id and bt_span:
       trace_id = bt_span.id
   # AFTER:
   trace_id = trace_id or bt_span.id
   ```

6. **`_run_guardrail` signature** — `bt_span` type changes from `Any | None` to `Any`.

#### `server/chat/V2/views.py`

1. **`_create_traced_executor`** — always use `TracedThreadPoolExecutor`:
   ```python
   # BEFORE:
   if _bt_config.enabled:
       return braintrust.TracedThreadPoolExecutor(max_workers=1)
   return concurrent.futures.ThreadPoolExecutor(max_workers=1)
   # AFTER:
   return braintrust.TracedThreadPoolExecutor(max_workers=1)
   ```
   (`TracedThreadPoolExecutor` passes through without tracing when no logger is active.)

2. **`_bt_logger` init** — keep this as the **single conditional** for module-level logger:
   ```python
   _bt_logger = (
       braintrust.init_logger(project=_bt_config.project, api_key=_bt_config.api_key)
       if _bt_config.enabled
       else None
   )
   ```
   The `if bt_logger:` guard before `bt_logger.update_span(...)` stays (unavoidable since
   `None.update_span()` would fail and the Braintrust SDK doesn't have a null logger object).

#### `server/chat/V2/utils.py`

1. **`flush_braintrust`** — remove early return guard:
   ```python
   # BEFORE:
   def flush_braintrust() -> None:
       if not get_braintrust_config().enabled:
           return
       braintrust.flush()
   # AFTER:
   def flush_braintrust() -> None:
       braintrust.flush()
   ```

### Net result
Reduces if-else checks from ~10 scattered guards to **2 init-only conditionals**:
- `_setup_braintrust_tracing`: `if not self.braintrust_logging_enabled: return`
- `views.py` module level: `_bt_logger = ... if _bt_config.enabled else None`

---

## Problem 2: Factory pattern for agent service (mock vs real Anthropic)

### Root cause
Currently, switching to mock Anthropic requires setting `ANTHROPIC_BASE_URL` in the
environment, which affects ALL requests globally. This forces a separate environment for
load testing vs production.

### Solution: Factory with `is_load_testing` + `base_url` param

The factory `get_agent_service(is_load_testing)` selects which Anthropic endpoint to use.
The Claude `ClaudeAgentService` gets a `base_url` param that overrides the SDK endpoint.

#### `server/chat/V2/utils.py`

Add `base_url` to `get_anthropic_client`:
```python
def get_anthropic_client(api_key: str | None = None, base_url: str | None = None) -> anthropic.Anthropic:
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY is required")
    kwargs: dict = {"api_key": key}
    if base_url:
        kwargs["base_url"] = base_url
    return anthropic.Anthropic(**kwargs)
```

#### `server/chat/V2/agent/claude_service.py`

1. Add `base_url` param to `ClaudeAgentService.__init__`:
   ```python
   def __init__(
       self,
       api_key: str | None = None,
       base_url: str | None = None,  # NEW
       model: str | None = None,
       ...
   ):
       self.client = get_anthropic_client(api_key, base_url=base_url)
       ...
       # Also pass base_url to SDK env if needed:
       if base_url and not os.environ.get("ANTHROPIC_BASE_URL"):
           os.environ["ANTHROPIC_BASE_URL"] = base_url
   ```

   > **Note:** The Claude Agent SDK reads `ANTHROPIC_BASE_URL` from env.
   > Setting it here (only when not already set) allows the SDK to route to mock.

2. Update `get_agent_service`:
   ```python
   _MOCK_ANTHROPIC_URL = os.environ.get("MOCK_ANTHROPIC_URL", "http://mock-anthropic:8002")

   def get_agent_service(is_load_testing: bool = False) -> ClaudeAgentService:
       """Create a fresh agent service instance (one per request).

       Pass is_load_testing=True to route requests to the mock Anthropic server
       instead of the real API — no environment variable switch required.
       """
       if is_load_testing:
           return ClaudeAgentService(base_url=_MOCK_ANTHROPIC_URL)
       return ClaudeAgentService()
   ```

#### `server/chat/V2/views.py` and `anthropic_views.py`

Read `IS_LOAD_TESTING` from env at the request level:
```python
import os

_IS_LOAD_TESTING = os.environ.get("IS_LOAD_TESTING", "false").lower() == "true"

# In the view:
agent = get_agent_service(is_load_testing=_IS_LOAD_TESTING)
```

#### `docker-compose.yml`

Replace `ANTHROPIC_BASE_URL` with:
```yaml
IS_LOAD_TESTING: "true"
MOCK_ANTHROPIC_URL: "http://mock-anthropic:8002"
```
Remove `ANTHROPIC_BASE_URL` from load test configs.

### Why not pass `is_load_testing` from request body?

The load test script fires HTTP requests at the same endpoint as real users.
An env var at the deployment level is cleaner — it means "this deployment is a
load test deployment" rather than trusting per-request user input.

---

## Files to Change

| File | Changes |
|------|---------|
| `server/chat/V2/utils.py` | Remove guard in `flush_braintrust`; add `base_url` to `get_anthropic_client` |
| `server/chat/V2/agent/claude_service.py` | Remove all `if bt_span:` guards; factory pattern for `get_agent_service` |
| `server/chat/V2/views.py` | Simplify `_create_traced_executor`; add `_IS_LOAD_TESTING` |
| `server/chat/V2/anthropic_views.py` | Add `_IS_LOAD_TESTING` |
| `docker-compose.yml` | Replace `ANTHROPIC_BASE_URL` with `IS_LOAD_TESTING` + `MOCK_ANTHROPIC_URL` |
| `server/CLAUDE.md` | Update env var docs |

## Commit sequence

1. Problem 1 (Braintrust no-op refactor) — one commit
2. Problem 2 (factory pattern) — one commit
