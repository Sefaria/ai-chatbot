# LC Chatbot

Embeddable AI chatbot for Jewish text learning. Claude + Sefaria API.
Ships to millions of users via Sefaria.org.

## Standards

**Production-ready code only.**

- Small, atomic commits after each logical change
- Keep code simple and clean - prefer conciseness over robustness
- Test before committing (pytest for backend, verify frontend)
- Add tests when adding features

## Testing Patterns

- Backend: `pytest` (SQLite by default, fast)
- Frontend: Verify changes manually, build succeeds with `npm run build`
- Run tests before committing

## Documentation Structure

This project uses a tree of CLAUDE.md files:

- `CLAUDE.md` (this file) - Project overview and standards
- `src/CLAUDE.md` - Frontend context (Svelte, Vite)
- `server/CLAUDE.md` - Backend context (Django, Claude API)
- `docs/ARCHITECTURE.md` - System design, API reference
- `docs/TESTING.md` - Test commands and CI details
- `docs/archive/` - Historical implementation docs

When working on implementation history or planning:
- Check `docs/archive/` for past decisions
- Store new plans in `docs/plans/`
- Move completed or obsolete plans to `docs/archive/`
- Update the relevant plan doc as part of each commit (mark completed items, note decisions made)
- Plans should stay in sync with the code — if you change the approach, update the plan

## Quick Reference

```bash
./setup.sh               # Install deps, create venv, run migrations
./start.sh               # Start backend + frontend
pytest                   # Backend tests
npm run dev              # Frontend dev server
npm run build            # Build bundle
```

**Note:** If tests fail with `ModuleNotFoundError: No module named 'sefaria'`:
`DJANGO_SETTINGS_MODULE=chatbot_server.test_settings pytest`

## Architecture

```
Svelte Web Component → Django REST → Claude Agent SDK → Sefaria API
```

See `docs/ARCHITECTURE.md` for detailed system design.

## Stack

- **Frontend:** Svelte 5, Vite, Web Components
- **Backend:** Django 4.2, DRF, PostgreSQL
- **AI:** Claude (Anthropic), Braintrust (prompts)

## Env

Required: `ANTHROPIC_API_KEY`, `BRAINTRUST_API_KEY`

Optional: `DB_*`
