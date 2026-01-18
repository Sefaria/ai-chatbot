## Summary

Developer experience improvements: documentation, tooling, comprehensive test infrastructure, and Braintrust logging cleanup.

## Changes

### Braintrust Logging Restructure
Implemented eval-ready structured logging per `docs/BRAINTRUST_RESTRUCTURE_PLAN.md`:
- **Structured input:** `{query, messages[]}` instead of truncated string
- **Structured output:** `{response, refs[], tool_calls[], was_refused}`
- **Tags:** Flow type + environment for filtering in Braintrust UI
- **Page context:** site, page_type, page_url for traffic segmentation
- **Refusal logging:** Previously invisible - now logged with full context
- Added `extract_page_type()` and `extract_refs()` helpers with 17 tests

Cleanup:
- Removed unused `server/chat/logging/` module
- Removed unused `server/chat/agent/claude_service_old.py`

### Documentation & Tooling
- Added `CLAUDE.md` with development standards, project overview, and setup instructions
- Added `ARCHITECTURE.md` with detailed system design, flows, components, and data models
- Added `.pre-commit-config.yaml` with ruff (Python) and eslint (JS/Svelte) hooks
- Added `setup.sh` - one-command setup (pyenv support, venv, deps, migrations, env checks)
- Added `start.sh` - starts backend + frontend with pre-flight checks and log tailing

### Test Infrastructure
- Added `server/pyproject.toml` with pytest and ruff configuration
- Added `server/conftest.py` for pytest Django settings
- Added `server/chatbot_server/test_settings.py` - SQLite in-memory test database
- Added pytest, pytest-django, and pytest-asyncio to requirements.txt
- Configured asyncio_mode = "auto" for async test support

### Test Suite (272 tests)
- `chat/tests/test_router_service.py` - 45 tests for flow classification, tool/prompt selection
- `chat/tests/test_guardrails.py` - 44 tests for pattern detection (injection, harassment, high-risk)
- `chat/tests/test_models.py` - 40 tests for Django models (ChatSession, ChatMessage, etc.)
- `chat/tests/test_serializers.py` - 22 tests for request/response serializers
- `chat/tests/test_tool_executor.py` - 35 tests for tool dispatch and error handling
- `chat/tests/test_prompt_service.py` - 30 tests for prompt caching and Braintrust integration
- `chat/tests/test_reason_codes.py` - 39 tests for reason code enumeration and filtering
- `chat/tests/test_braintrust_helpers.py` - 17 tests for Braintrust logging helpers

## Test Plan

- [x] Run backend tests with pytest (272 passed)
- [x] Verify local setup works end-to-end (SQLite, migrations, server start, frontend build)
- [x] Test setup.sh with pyenv
- [x] Test start.sh launches both servers correctly
- [ ] Install and test pre-commit hooks

## Running Tests

```bash
cd server
./venv/bin/python -m pytest chat/tests/ -v
```

## Notes

Branch: `daniel-init-playground`
