# LC Chatbot

Embeddable AI chatbot for Jewish text learning. Claude + Sefaria API.

## Standards

**Production-ready code only.** This ships to millions of users.

- Small, atomic commits. Commit after each logical change.
- Keep code simple and clean. Prefer conciseness over robustness.
- Test before committing. Run `pytest` (backend) and verify frontend changes.
- When adding features, add tests. Fix flaky/broken tests when encountered.
- Plans should use clear prose paragraphs, not code examples. Describe the approach and reasoning in plain language.

## Quick Start

```bash
./setup.sh   # Install deps, create venv, run migrations
./start.sh   # Start backend + frontend
```

## Commands

```bash
pytest                                    # Backend tests (SQLite, fast)
python manage.py runserver 0.0.0.0:8001  # Backend server
npm run dev                               # Frontend dev server
npm run build                             # Build bundle
```

**Note:** If tests fail with `ModuleNotFoundError: No module named 'sefaria'`, prefix with:
`DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest`

See [TESTING.md](TESTING.md) for PostgreSQL testing and CI details.

## Architecture

```
Svelte Web Component → Django REST → Router → Claude Agent → Sefaria API
```

**Flows:** HALACHIC | SEARCH | GENERAL | REFUSE

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed system design.

## Stack

- **Frontend:** Svelte 5, Vite, Web Components
- **Backend:** Django 4.2, DRF, PostgreSQL
- **AI:** Claude (Anthropic), Braintrust (prompts), LangSmith (tracing)

## Structure

```
src/components/LCChatbot.svelte  # Main widget
src/lib/                         # API, session, markdown

server/chat/views.py             # API endpoints
server/chat/V2/router/           # Intent classification
server/chat/V2/agent/            # Claude + tools
```

## Env

Required: `ANTHROPIC_API_KEY`

Optional: `BRAINTRUST_API_KEY`, `LANGSMITH_API_KEY`, `DB_*`
