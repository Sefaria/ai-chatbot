# Braintrust Origin Tagging & Logging Toggle

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Tag every Braintrust trace with its origin (who sent the request) and add a "dev" tag to non-production traces, so production user traffic is clearly separated from eval/dev/testing traffic. Also add an env var to disable Braintrust logging entirely.

**Architecture:** Callers identify themselves via a `context.origin` field in the request body (streaming endpoint) or `X-Origin` header (anthropic endpoint). The server has a hardcoded default origin (`DEFAULT_ORIGIN = "dev"`). If the resolved origin is not in a hardcoded production list (`PROD_ORIGINS = ["sefaria-prod"]`), the span gets a `"dev"` Braintrust tag. The origin string always goes into `metadata.origin`. A separate `BRAINTRUST_LOGGING_ENABLED` env var (defaults to `"true"`) can disable all Braintrust tracing.

**Tech Stack:** Django, Braintrust Python SDK (`span.log(tags=..., metadata=...)`)

**Status:** COMPLETE — All 8 tasks implemented and tested. Merged with main's agent refactor (claude_service.py split into focused modules) and load-test tracing guard. `tracing_runtime.py` removed (superseded by inline check in `claude_service.py`). Ready for deployment after sefaria-project sends `origin="sefaria-prod"`.

---

## Design Decisions

- **Default origin is `"dev"`** — hardcoded constant. If a caller forgets to send an origin, traces are tagged as dev. Safe default.
- **Production origins are hardcoded** — `PROD_ORIGINS = ["sefaria-prod"]`. Easy to add more later by editing the list.
- **Production sefaria-project must explicitly send `origin: "sefaria-prod"`** — this is deployed to sefaria-project FIRST (the field is ignored until this repo is deployed).
- **`tags: ["dev"]`** only on non-production traces. Production traces stay clean (no tags). This lets Braintrust users filter by excluding the "dev" tag.
- **`metadata.origin`** is always set to the resolved origin string, for structured queries.
- **Origin is free-form** — no validation or enum. Any string is accepted.
- **Logging toggle** — `BRAINTRUST_LOGGING_ENABLED=false` env var skips all Braintrust SDK setup. The agent still works, spans are just no-ops.

## Constants (in `server/chat/V2/origin.py`)

| Constant | Value | Description |
|---|---|---|
| `DEFAULT_ORIGIN` | `"dev"` | Origin when caller sends nothing |
| `PROD_ORIGINS` | `["sefaria-prod"]` | Origins that don't get the "dev" tag |

## Env Vars

| Variable | Default | Description |
|---|---|---|
| `BRAINTRUST_LOGGING_ENABLED` | `"true"` | Set to `"false"` to disable all Braintrust tracing |

---

### Task 1: Add origin resolution and MessageContext.origin

Create the origin resolution module with hardcoded constants, and add an `origin` field to `MessageContext`. Also add `BRAINTRUST_LOGGING_ENABLED` to Django settings.

**Files:**
- Create: `server/chat/V2/origin.py` (constants + resolve_origin helper)
- Modify: `server/chat/V2/agent/contracts.py:32-37` (add `origin` field to `MessageContext`)
- Modify: `server/chatbot_server/settings.py` (add `BRAINTRUST_LOGGING_ENABLED` env var)

**Step 1: Write test for origin resolution helper**

```python
# server/chat/tests/test_origin.py
from chat.V2.origin import resolve_origin


class TestResolveOrigin:
    """Test origin resolution from caller-provided value + hardcoded defaults."""

    def test_no_caller_origin_returns_default(self):
        origin, is_prod = resolve_origin(None)
        assert origin == "dev"
        assert is_prod is False

    def test_empty_string_returns_default(self):
        origin, is_prod = resolve_origin("")
        assert origin == "dev"
        assert is_prod is False

    def test_caller_origin_used_as_is(self):
        origin, is_prod = resolve_origin("my-test-tool")
        assert origin == "my-test-tool"
        assert is_prod is False

    def test_prod_origin_detected(self):
        origin, is_prod = resolve_origin("sefaria-prod")
        assert origin == "sefaria-prod"
        assert is_prod is True

    def test_non_prod_origin(self):
        origin, is_prod = resolve_origin("eval")
        assert origin == "eval"
        assert is_prod is False

    def test_whitespace_stripped(self):
        origin, is_prod = resolve_origin("  sefaria-prod  ")
        assert origin == "sefaria-prod"
        assert is_prod is True
```

**Step 2: Run test to verify it fails**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest server/chat/tests/test_origin.py -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_origin' from 'chat.V2.origin'`

**Step 3: Implement origin module, MessageContext, and settings**

Create `server/chat/V2/origin.py`:

```python
"""Origin resolution for Braintrust trace tagging."""

DEFAULT_ORIGIN = "dev"
PROD_ORIGINS = ["sefaria-prod"]


def resolve_origin(caller_origin: str | None) -> tuple[str, bool]:
    """Resolve the trace origin from caller-provided value and defaults.

    Returns (origin_string, is_production).
    """
    origin = (caller_origin or "").strip() or DEFAULT_ORIGIN
    is_prod = origin in PROD_ORIGINS
    return origin, is_prod
```

In `server/chat/V2/agent/contracts.py`, add `origin` to `MessageContext`:

```python
@dataclass
class MessageContext:
    """Sideband context injected into the system prompt (not part of messages)."""
    summary_text: str | None = None
    page_url: str | None = None
    session_id: str | None = None
    origin: str | None = None
```

In `server/chatbot_server/settings.py`, add after the Braintrust section (~line 168):

```python
BRAINTRUST_LOGGING_ENABLED = os.environ.get("BRAINTRUST_LOGGING_ENABLED", "true").lower() == "true"
```

**Step 4: Run test to verify it passes**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest server/chat/tests/test_origin.py -v`
Expected: All 6 tests PASS

**Step 5: Commit**

```bash
git add server/chat/V2/origin.py server/chat/V2/agent/contracts.py server/chatbot_server/settings.py server/chat/tests/test_origin.py
git commit -m "feat: add origin resolution helper and MessageContext.origin field"
```

---

### Task 2: Wire origin into BraintrustTraceLogger

Update the trace logger to include `origin` in metadata and conditionally add a `"dev"` tag on non-production traces.

**Files:**
- Modify: `server/chat/V2/agent/trace_logger.py:14-30` (update `log_input` to accept and log origin)

**Step 1: Write test for trace logger origin logging**

Add tests to the existing test file or create a new one. The key behavior: `log_input` should add `metadata.origin` always, and `tags=["dev"]` when not production.

```python
# server/chat/tests/test_trace_logger.py
from unittest.mock import MagicMock

from chat.V2.agent.contracts import MessageContext
from chat.V2.agent.trace_logger import BraintrustTraceLogger


class TestTraceLoggerOrigin:
    """Test that BraintrustTraceLogger logs origin metadata and dev tag."""

    def setup_method(self):
        self.logger = BraintrustTraceLogger()
        self.span = MagicMock()

    def test_non_prod_origin_logs_dev_tag(self):
        ctx = MessageContext(origin="local")
        self.logger.log_input(
            bt_span=self.span, user_message="hi", context=ctx, model="test", is_prod=False
        )
        call_kwargs = self.span.log.call_args[1]
        assert call_kwargs["metadata"]["origin"] == "local"
        assert call_kwargs["tags"] == ["dev"]

    def test_prod_origin_logs_no_tag(self):
        ctx = MessageContext(origin="sefaria-prod")
        self.logger.log_input(
            bt_span=self.span, user_message="hi", context=ctx, model="test", is_prod=True
        )
        call_kwargs = self.span.log.call_args[1]
        assert call_kwargs["metadata"]["origin"] == "sefaria-prod"
        assert "tags" not in call_kwargs

    def test_origin_always_in_metadata(self):
        ctx = MessageContext(origin="eval-runner")
        self.logger.log_input(
            bt_span=self.span, user_message="hi", context=ctx, model="test", is_prod=False
        )
        call_kwargs = self.span.log.call_args[1]
        assert call_kwargs["metadata"]["origin"] == "eval-runner"
```

**Step 2: Run test to verify it fails**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest server/chat/tests/test_trace_logger.py -v`
Expected: FAIL — `log_input() got an unexpected keyword argument 'is_prod'`

**Step 3: Update trace_logger.py**

Update `log_input` in `server/chat/V2/agent/trace_logger.py` to accept `is_prod` and log origin/tags:

```python
def log_input(
    self,
    *,
    bt_span: Any,
    user_message: str,
    context: MessageContext,
    model: str,
    is_prod: bool = False,
) -> None:
    span_input: dict[str, Any] = {"message": user_message}
    if context.page_url:
        span_input["page_url"] = context.page_url
    if context.summary_text:
        span_input["summary"] = context.summary_text
    span_metadata: dict[str, Any] = {"model": model}
    if context.session_id:
        span_metadata["session_id"] = context.session_id
    if context.origin:
        span_metadata["origin"] = context.origin

    log_kwargs: dict[str, Any] = {"input": span_input, "metadata": span_metadata}
    if not is_prod:
        log_kwargs["tags"] = ["dev"]
    bt_span.log(**log_kwargs)
```

**Step 4: Run test to verify it passes**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest server/chat/tests/test_trace_logger.py -v`
Expected: All 3 tests PASS

**Step 5: Commit**

```bash
git add server/chat/V2/agent/trace_logger.py server/chat/tests/test_trace_logger.py
git commit -m "feat: log origin metadata and dev tag in BraintrustTraceLogger"
```

---

### Task 3: Wire origin through the orchestrator

The `TurnOrchestrator.run_turn` calls `trace_logger.log_input`. It needs to resolve the origin and pass `is_prod` through.

**Files:**
- Modify: `server/chat/V2/agent/turn_orchestrator.py:67-72` (pass `is_prod` to `log_input`)

**Step 1: Update turn_orchestrator.py**

In `run_turn`, resolve origin and pass `is_prod` to `log_input`:

```python
from ..origin import resolve_origin

# Inside run_turn, before the log_input call:
_, is_prod = resolve_origin(context.origin)

self.trace_logger.log_input(
    bt_span=bt_span,
    user_message=last_user_message,
    context=context,
    model=self.model,
    is_prod=is_prod,
)
```

**Step 2: Run existing tests to verify nothing breaks**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest server/chat/tests/ -v`
Expected: All existing tests PASS

**Step 3: Commit**

```bash
git add server/chat/V2/agent/turn_orchestrator.py
git commit -m "feat: pass origin through orchestrator to trace logger"
```

---

### Task 4: Wire origin from view layers (streaming + anthropic endpoints)

Both endpoints construct `MessageContext`. They need to read the caller-provided origin and pass it through.

**Files:**
- Modify: `server/chat/V2/views.py:110,138-142` (read `X-Origin` header, set `msg_context.origin`)
- Modify: `server/chat/V2/anthropic_views.py:210,241` (read `origin` from request metadata, set `msg_context.origin`)
- Modify: `server/chat/serializers.py:10-15` (add `origin` to `MessageContextSerializer`)
- Modify: `server/chatbot_server/settings.py:124-127` (add `X-Origin` to CORS allowed headers)

**Step 1: Write tests for view-layer origin wiring**

Add to existing test files. Key behavior: the endpoint reads origin from the right place and passes it to the agent service.

For the streaming endpoint, origin comes from the `context.origin` field in the request body (alongside `pageUrl`, `locale`, etc.). For the anthropic endpoint, origin comes from the `X-Origin` header.

```python
# Add to server/chat/tests/test_anthropic_views.py

class TestAnthropicOriginHeader:
    """Test that X-Origin header is passed through to MessageContext."""

    @patch("chat.V2.anthropic_views.get_agent_service")
    @patch("chat.V2.anthropic_views.authenticate_request")
    def test_origin_header_passed_to_context(self, mock_auth, mock_get_agent):
        mock_auth.return_value = MagicMock(user_id="test-user")
        mock_agent = MagicMock()
        mock_agent.send_message = AsyncMock(return_value=AgentResponse(
            content="ok", tool_calls=[], latency_ms=100
        ))
        mock_get_agent.return_value = mock_agent

        factory = APIRequestFactory()
        request = factory.post(
            "/api/v2/chat/anthropic",
            {"model": "test", "max_tokens": 100, "messages": [{"role": "user", "content": "hi"}]},
            format="json",
            HTTP_X_ORIGIN="eval-runner",
        )
        with patch("chat.V2.anthropic_views.create_or_get_session") as mock_session:
            mock_session.return_value = (MagicMock(turn_count=0), True)
            with patch("chat.V2.anthropic_views.save_user_message"):
                with patch("chat.V2.anthropic_views._flush_braintrust"):
                    with patch("chat.V2.anthropic_views.get_turn_logging_service"):
                        chat_anthropic_v2(request)

        ctx = mock_agent.send_message.call_args[1]["context"]
        assert ctx.origin == "eval-runner"
```

**Step 2: Run test to verify it fails**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest server/chat/tests/test_anthropic_views.py::TestAnthropicOriginHeader -v`
Expected: FAIL — `ctx.origin` is None or AttributeError

**Step 3: Implement view-layer changes**

In `server/chat/V2/views.py`, read origin from the request context and pass it to `MessageContext`:

```python
# In chat_stream_v2, around line 138:
from .origin import resolve_origin

caller_origin = context.get("origin")
resolved_origin, _ = resolve_origin(caller_origin)

msg_context = MessageContext(
    summary_text=summary_text,
    page_url=page_url or None,
    session_id=data["sessionId"],
    origin=resolved_origin,
)
```

In `server/chat/V2/anthropic_views.py`, read `X-Origin` header (this endpoint is used by Braintrust playground/evals, not the Svelte frontend):

```python
# In chat_anthropic_v2, around line 241:
from .origin import resolve_origin

caller_origin = request.headers.get("X-Origin")
resolved_origin, _ = resolve_origin(caller_origin)

msg_context = MessageContext(
    summary_text=summary_text or None,
    session_id=session_id,
    origin=resolved_origin,
)
```

In `server/chat/serializers.py`, add `origin` to `MessageContextSerializer`:

```python
class MessageContextSerializer(serializers.Serializer):
    pageUrl = serializers.URLField(required=False, allow_blank=True)
    locale = serializers.CharField(max_length=10, required=False, allow_blank=True)
    clientVersion = serializers.CharField(max_length=20, required=False, allow_blank=True)
    origin = serializers.CharField(max_length=100, required=False, allow_blank=True)
```

In `server/chatbot_server/settings.py`, add `X-Origin` to the CORS allowed headers:

```python
CORS_ALLOW_HEADERS = list(default_headers) + [
    "sentry-trace",
    "baggage",
    "x-origin",
]
```

**Step 4: Run tests to verify they pass**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest server/chat/tests/test_anthropic_views.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add server/chat/V2/views.py server/chat/V2/anthropic_views.py server/chat/serializers.py server/chatbot_server/settings.py
git commit -m "feat: wire origin from request headers/body into MessageContext"
```

---

### Task 5: Add Braintrust logging toggle

Add the ability to disable Braintrust logging entirely via `BRAINTRUST_LOGGING_ENABLED=false`.

**Files:**
- Modify: `server/chat/V2/agent/claude_service.py:119-130` (skip braintrust setup when disabled)
- Modify: `server/chat/V2/views.py:66-70` (guard `init_logger` call)

**Step 1: Write test for logging toggle**

```python
# Add to server/chat/tests/test_braintrust_tracing.py

class TestBraintrustLoggingToggle:
    """Verify BRAINTRUST_LOGGING_ENABLED=false skips SDK setup."""

    def test_setup_skipped_when_disabled(self):
        from chat.V2.agent import claude_service

        mock_setup = MagicMock()
        with patch.dict("os.environ", {"BRAINTRUST_API_KEY": "test-key", "ANTHROPIC_API_KEY": "test"}):
            with patch("chat.V2.agent.claude_service.setup_claude_agent_sdk", mock_setup):
                with override_settings(BRAINTRUST_LOGGING_ENABLED=False):
                    claude_service._BRAINTRUST_SETUP_DONE = False
                    try:
                        service = claude_service.ClaudeAgentService()
                        service._setup_braintrust_tracing()
                    except Exception:
                        pass
                    mock_setup.assert_not_called()
```

**Step 2: Run test to verify it fails**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest server/chat/tests/test_braintrust_tracing.py::TestBraintrustLoggingToggle -v`
Expected: FAIL — `setup_claude_agent_sdk` is still called

**Step 3: Implement the toggle**

In `server/chat/V2/agent/claude_service.py`, update `_setup_braintrust_tracing`:

```python
def _setup_braintrust_tracing(self) -> None:
    from django.conf import settings as django_settings

    if not django_settings.BRAINTRUST_LOGGING_ENABLED:
        return

    global _BRAINTRUST_SETUP_DONE
    if not _BRAINTRUST_SETUP_DONE:
        setup_claude_agent_sdk(project=self.braintrust_project, api_key=self.braintrust_api_key)
        _BRAINTRUST_SETUP_DONE = True
    elif not braintrust.current_logger():
        braintrust.init_logger(project=self.braintrust_project, api_key=self.braintrust_api_key)
```

In `server/chat/V2/views.py`, guard the module-level logger init (~line 69):

```python
from django.conf import settings as django_settings

if django_settings.BRAINTRUST_LOGGING_ENABLED:
    _bt_config = get_braintrust_config()
    _bt_logger = braintrust.init_logger(project=_bt_config.project, api_key=_bt_config.api_key)
else:
    _bt_logger = None
```

Also guard the feedback endpoint's usage of `_bt_logger` — if it's None, return a 503 or skip the Braintrust update.

**Step 4: Run tests to verify they pass**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest server/chat/tests/ -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add server/chat/V2/agent/claude_service.py server/chat/V2/views.py
git commit -m "feat: add BRAINTRUST_LOGGING_ENABLED toggle to skip tracing setup"
```

---

### Task 6: Update evals to send origin

The eval runner (`evals/run_eval.py`) hits the streaming endpoint. It should send an origin so its traces are tagged correctly.

**Files:**
- Modify: `evals/run_eval.py:66-73` (add `origin` to request context)

**Step 1: Update the eval client**

In `ChatbotClient.chat()`, add origin to the payload context:

```python
payload = {
    "sessionId": session_id,
    "messageId": f"msg_{uuid.uuid4().hex[:16]}",
    "text": message,
    "userId": USER_TOKEN,
    "timestamp": datetime.now().isoformat(),
    "context": {
        "origin": "eval",
    },
}
```

**Step 2: Verify manually**

Run: `python evals/run_eval.py --local -d Benchmark -e "test-origin" --all-scorers` (or a quick manual test)
Check Braintrust UI: the trace should have `metadata.origin: "eval"` and `tags: ["dev"]`.

**Step 3: Commit**

```bash
git add evals/run_eval.py
git commit -m "feat: send origin=eval from eval runner"
```

---

### Task 7: Update BRAINTRUST_ORIGIN in anthropic_views.py

The existing `BRAINTRUST_ORIGIN = "braintrust"` constant is used in response metadata and as the DB `current_flow`. Now that origin is dynamic, this needs updating. The response metadata should reflect the resolved origin, and the DB flow field should use the resolved origin too.

**Files:**
- Modify: `server/chat/V2/anthropic_views.py:50,131,145,218,224,238` (use resolved origin instead of hardcoded constant)

**Step 1: Update anthropic_views.py**

Replace the hardcoded `BRAINTRUST_ORIGIN` usage with the resolved origin. Keep the constant as a fallback default for backwards compatibility in the response format, but use the resolved origin for DB flow and response metadata:

```python
# The resolved_origin is already computed (from Task 4).
# Use it in to_anthropic_response and to_anthropic_error calls,
# and in create_or_get_session / save_user_message flow= params.
```

The `to_anthropic_response` and `to_anthropic_error` functions should accept `origin` as a parameter instead of using the hardcoded constant.

**Step 2: Run existing anthropic view tests**

Run: `DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest server/chat/tests/test_anthropic_views.py -v`
Expected: All tests PASS (update any assertions that check for `"braintrust"` origin)

**Step 3: Commit**

```bash
git add server/chat/V2/anthropic_views.py
git commit -m "feat: use resolved origin in anthropic endpoint responses and DB flow"
```

---

### Task 8: Document and note sefaria-project change

**Files:**
- Create: `docs/plans/sefaria-project-origin-prompt.md` (prompt for sefaria-project implementation)
- Modify: `server/docs/BRAINTRUST_TRACING.md` (add origin tagging section)

**Step 1: Write sefaria-project implementation prompt**

See below for the full content.

**Step 2: Update BRAINTRUST_TRACING.md**

Add a section about origin tagging, the env vars, and how callers should send origins.

**Step 3: Commit**

```bash
git add docs/plans/sefaria-project-origin-prompt.md server/docs/BRAINTRUST_TRACING.md
git commit -m "docs: add origin tagging docs and sefaria-project change prompt"
```

---

## Deployment Order

1. **Deploy sefaria-project first** — add `origin: "sefaria-prod"` to the chat request context. The field is silently ignored by the current chatbot (DRF serializer drops unknown fields). Zero risk.
2. **Deploy this repo** — once deployed, the chatbot reads `context.origin` and tags traces accordingly. Since sefaria-project is already sending `"sefaria-prod"`, production traces are immediately clean.

---

## sefaria-project Change

Sefaria-project renders the `<lc-chatbot>` web component. Add the `origin` attribute:

```html
<lc-chatbot
  user-id="..."
  api-base-url="..."
  origin="sefaria-prod"
></lc-chatbot>
```

The `origin` attribute is a plain string passed as a web component prop. The chatbot component handles forwarding it in its API requests — sefaria-project doesn't need to touch any API calls.

If there are other Sefaria deployments (staging, Israel site, etc.), use different origin values for each, e.g. `"sefaria-staging"` or `"sefaria-il"`. The value is free-form, but only `"sefaria-prod"` is treated as production (no "dev" tag in Braintrust).

This change is safe to deploy immediately — the current chatbot component ignores unknown attributes. Once the chatbot repo deploys its side, the attribute will be read and forwarded automatically.
