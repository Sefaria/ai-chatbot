## Summary

Developer experience improvements and Braintrust logging restructure for eval-ready data.

## Changes at a Glance

- **Braintrust logging** - Structured input/output, tags, refusal logging (see details below)
- **Documentation** - `CLAUDE.md`, `ARCHITECTURE.md`
- **Tooling** - `setup.sh`, `start.sh`, `.pre-commit-config.yaml`
- **Test infrastructure** - pytest config, test settings, 272 tests
- **Cleanup** - Removed unused logging module and old service file

---

## Braintrust Logging Restructure

Implemented eval-ready structured logging. See `docs/BRAINTRUST_RESTRUCTURE_PLAN.md` for full details (temp doc, will be removed after merge).

**Before → After:**
| Field | Before | After |
|-------|--------|-------|
| `input` | Truncated string | `{query, messages[]}` |
| `output` | String | `{response, refs[], tool_calls[], was_refused}` |
| `tags` | None | `[flow, environment]` |
| Refusals | Not logged | Fully logged with reason codes |

**Files changed:**
- `server/chat/views.py` - Added `extract_page_type()`, `extract_page_context()`
- `server/chat/agent/claude_service.py` - Restructured span.log, added `extract_refs()`, refusal logging

---

## Documentation & Tooling

| File | Purpose |
|------|---------|
| `CLAUDE.md` | Development standards, project overview, quick start |
| `ARCHITECTURE.md` | System design, flows, components, data models |
| `setup.sh` | One-command setup (pyenv, venv, deps, migrations) |
| `start.sh` | Starts backend + frontend with pre-flight checks |
| `.pre-commit-config.yaml` | ruff (Python) + eslint (JS/Svelte) hooks |

---

## Test Infrastructure

- `server/pyproject.toml` - pytest and ruff configuration
- `server/conftest.py` - Django test settings
- `server/chatbot_server/test_settings.py` - SQLite in-memory database
- Added pytest, pytest-django, pytest-asyncio to requirements.txt

---

## Test Suite (272 tests)

| File | Tests | Coverage |
|------|-------|----------|
| `test_router_service.py` | 45 | Flow classification, tool/prompt selection |
| `test_guardrails.py` | 44 | Pattern detection (injection, harassment) |
| `test_models.py` | 40 | Django models |
| `test_tool_executor.py` | 35 | Tool dispatch, error handling |
| `test_reason_codes.py` | 39 | Reason code enumeration |
| `test_prompt_service.py` | 30 | Prompt caching, Braintrust integration |
| `test_serializers.py` | 22 | Request/response serializers |
| `test_braintrust_helpers.py` | 17 | extract_page_type, extract_refs |

---

## Test Plan

- [x] Run backend tests with pytest (272 passed)
- [x] Verify local setup works end-to-end
- [x] Test setup.sh with pyenv
- [x] Test start.sh launches both servers
- [ ] Install and test pre-commit hooks
- [ ] Verify Braintrust logs in UI after deployment

## Running Tests

```bash
cd server && ./venv/bin/python -m pytest chat/tests/ -v
```
