# Testing

## Quick Reference

```bash
pytest                                              # Local (SQLite, fast)
pytest --ds=chatbot_server.test_settings_postgres   # Local (PostgreSQL)
```

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
