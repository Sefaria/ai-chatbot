# Testing

## Quick Reference

```bash
pytest                                              # Local (SQLite, fast)
pytest --ds=chatbot_server.test_settings_postgres   # Local (PostgreSQL)
```

Database-backed tests need PostgreSQL for now: an existing migration
(`chat/0009_saved_conversation_metadata`) uses Postgres-only SQL, so on SQLite they error
in setup. Run them with `--ds=chatbot_server.test_settings_postgres`, as CI does.

The skill catalog has its own check, also run in CI: `python -m skills_catalog.validate`.

## Strategy

- **Local development**: SQLite in-memory (fast, no setup)
- **CI**: PostgreSQL (matches production, catches DB-specific issues)

## Local PostgreSQL Setup (Optional)

```bash
createdb ai_chatbot_test
pytest --ds=chatbot_server.test_settings_postgres
```

## Git Hooks

- **pre-commit**: Runs ruff linting on staged Python files
